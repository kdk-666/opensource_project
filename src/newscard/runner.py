"""수집부터 영상까지 한 번에 실행한다: newscard run

순서: top -> summarize -> verify -> script -> check -> cards -> tts -> video
- 시작 전에 필요한 것(Ollama와 모델, 한글 폰트, FFmpeg, Piper)을 한꺼번에 점검해서, 10분 돌리고 나서야 실패하는 일을 막는다.
- 단계가 실패하면 거기서 멈추고, 어디서부터 다시 하면 되는지 알려 준다. (--from 단계이름)
- 검토에서 문제가 발견돼도 기본은 계속 진행하고 마지막에 경고로 모아 보여 준다. --strict면 문제가 있을 때 멈춘다.
- 결과 요약은 data/run_report.json에 저장한다.
"""
import importlib.util
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class StepError(RuntimeError):
    """단계가 계속 진행할 수 없을 때 사람이 읽을 이유와 함께 던진다."""


@dataclass
class Context:
    themes: tuple[str, ...] = ("light", "dark")
    strict: bool = False
    data_dir: str = "data"


@dataclass
class StepResult:
    summary: str
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 단계
def step_top(ctx: Context) -> StepResult:
    from newscard.config import load_settings
    from newscard.pipeline import run

    events = run(load_settings())
    if not events:
        raise StepError("Top 사건을 하나도 뽑지 못했어요. RSS 수집이나 인터넷 연결을 확인하세요.")
    warns = [f"{e.rank}위 '{e.title[:20]}': " + "; ".join(e.notes) for e in events if e.notes]
    return StepResult(f"Top {len(events)} 사건 선정", warns)


def step_summarize(ctx: Context) -> StepResult:
    from newscard.summarize import run

    items = run()
    ok = [s for s in items if s.status == "ok"]
    if not ok:
        raise StepError("요약에 모두 실패했어요. Ollama가 켜져 있고 모델이 있는지 확인하세요: " + (items[0].error if items else ""))
    warns = [f"{s.rank}위 요약 실패: {s.error}" for s in items if s.status != "ok"]
    warns += [f"{s.rank}위는 제목만으로 요약함(제한적)" for s in ok if s.basis == "title_only"]
    return StepResult(f"요약 {len(ok)}/{len(items)}건 성공", warns)


def step_verify(ctx: Context) -> StepResult:
    from newscard.verify import run

    results = run()
    counts = {k: sum(1 for r in results if r.status == k) for k in ("pass", "warn", "flag", "skipped")}
    flagged = [r for r in results if r.status == "flag"]
    warns = [f"{r.rank}위 요약 검토 필요: " + "; ".join(r.rule_issues[:2]) for r in flagged]
    if ctx.strict and flagged:
        raise StepError("요약 검토에서 문제가 발견돼 멈췄어요(--strict):\n  " + "\n  ".join(warns))
    return StepResult(f"요약 검토 통과 {counts['pass']} / 주의 {counts['warn']} / 검토 필요 {counts['flag']}", warns)


def step_script(ctx: Context) -> StepResult:
    from newscard.script import run

    cards = run()
    if not cards:
        raise StepError("대본을 만들 요약이 없어요. 요약 단계를 확인하세요.")
    fb = [c for c in cards if c.generated_by == "fallback"]
    warns = [f"{c.rank}위 나레이션을 모델이 쓰지 못해 요약문으로 대체함" for c in fb]
    return StepResult(f"대본 {len(cards)}장 (인트로·아웃트로 포함)", warns)


def step_check(ctx: Context) -> StepResult:
    from newscard.script_check import run

    rep = run()
    cards = rep["cards"]
    flagged = [c for c in cards if c["status"] == "flag"]
    warns = [f"[{c['index']}] 대본 수정 필요: " + "; ".join(c["issues"][:2]) for c in flagged]
    warns += [f"[구조] {i}" for i in rep["structure_issues"]]
    if ctx.strict and (flagged or rep["structure_issues"]):
        raise StepError("대본 검사에서 문제가 발견돼 멈췄어요(--strict):\n  " + "\n  ".join(warns))
    return StepResult(f"대본 검사 수정 필요 {len(flagged)}장 / 영상 길이 약 {rep['total_est_sec']:.0f}초 추정", warns)


def step_cards(ctx: Context) -> StepResult:
    from newscard.cards import run

    result = run(themes=ctx.themes)
    warns = [f"[{t} {m['index']}] 글이 넘쳐 본문 {m['dropped_bullets']}줄을 뺌"
             for t, ms in result.items() for m in ms if m["dropped_bullets"]]
    n = len(next(iter(result.values())))
    return StepResult(f"카드 이미지 {n}장 x {len(result)}개 모드", warns)


def step_tts(ctx: Context) -> StepResult:
    from newscard.tts import run

    items = run()
    ok = [i for i in items if i["status"] == "ok"]
    if not ok:
        raise StepError("나레이션 음성을 하나도 만들지 못했어요: " + (items[0]["error"] if items else ""))
    warns = [f"[{i['index']}] 음성 실패: {i['error']}" for i in items if i["status"] != "ok"]
    warns += [f"[{i['index']}] {w}" for i in ok for w in i["warnings"]]
    total = sum(i["duration_sec"] for i in ok)
    return StepResult(f"음성 {len(ok)}/{len(items)}장, 합계 {total:.0f}초", warns)


def step_video(ctx: Context) -> StepResult:
    from newscard.video import run

    results = run(themes=ctx.themes)
    warns = [f"[{t}] {n}" for t, r in results.items() for n in r["notes"]]
    desc = ", ".join(f"{t} {r['duration']:.0f}초/{r['size'] / 1e6:.1f}MB" for t, r in results.items())
    return StepResult(f"영상 {len(results)}개 ({desc}) -> data/video/", warns)


STEPS: list[tuple[str, Callable[[Context], StepResult]]] = [
    ("top", step_top), ("summarize", step_summarize), ("verify", step_verify), ("script", step_script),
    ("check", step_check), ("cards", step_cards), ("tts", step_tts), ("video", step_video),
]
STEP_NAMES = [n for n, _ in STEPS]


# ---------------------------------------------------------------- 사전 점검
NEEDS_OLLAMA = {"summarize", "script"}


def preflight(names: list[str]) -> list[str]:
    """선택된 단계에 필요한 것이 갖춰졌는지 점검한다. 문제 목록(비어 있으면 통과)을 돌려준다."""
    problems = []
    if NEEDS_OLLAMA & set(names):
        from newscard.summarize import DEFAULT_MODEL

        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        model = os.environ.get("NEWSCARD_MODEL", DEFAULT_MODEL)
        try:
            import ollama

            listed = ollama.Client(host=host, timeout=10).list()
            models = [m["model"] if not hasattr(m, "model") else m.model for m in listed["models"]]
        except Exception as e:
            problems.append(f"Ollama({host})에 연결하지 못했어요: {type(e).__name__}. "
                            f"`docker compose up -d ollama`로 켜세요.")
        else:
            if not any(x == model or x.split(":")[0] == model for x in models):
                problems.append(f"Ollama에 모델 '{model}'이 없어요. "
                                f"`docker compose exec ollama ollama pull {model}`로 받으세요.")
    if "cards" in names:
        from newscard.cards import FontNotFound, find_fonts

        try:
            find_fonts()
        except FontNotFound as e:
            problems.append(str(e))
    if "video" in names and not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        problems.append("FFmpeg를 찾지 못했어요. Docker 이미지에 포함돼 있어요.")
    if "tts" in names and importlib.util.find_spec("piper") is None:
        problems.append("Piper(piper-tts)가 설치돼 있지 않아요. `docker compose build`로 이미지를 다시 만드세요.")
    return problems


# ---------------------------------------------------------------- 실행
def select_steps(start: str | None, end: str | None) -> list[str]:
    for name in (start, end):
        if name and name not in STEP_NAMES:
            raise ValueError(f"알 수 없는 단계: {name} (사용 가능: {', '.join(STEP_NAMES)})")
    i = STEP_NAMES.index(start) if start else 0
    j = STEP_NAMES.index(end) if end else len(STEP_NAMES) - 1
    if i > j:
        raise ValueError(f"--from({start})이 --until({end})보다 뒤에 있어요.")
    return STEP_NAMES[i : j + 1]


def fmt_sec(sec: float) -> str:
    return f"{sec:.1f}초" if sec < 60 else f"{int(sec // 60)}분 {int(sec % 60)}초"


def run_pipeline(ctx: Context, names: list[str], out: Callable[[str], None] = print) -> dict:
    steps = dict(STEPS)
    report = {"steps": [], "ok": True, "failed_step": "", "error": "", "warnings": [], "total_sec": 0.0}
    started = time.perf_counter()
    for n, name in enumerate(names, 1):
        out(f"[{n}/{len(names)}] {name} ...")
        t0 = time.perf_counter()
        entry = {"name": name, "status": "ok", "sec": 0.0, "summary": "", "warnings": []}
        try:
            res = steps[name](ctx)
            entry.update(summary=res.summary, warnings=res.warnings)
            out(f"      완료 ({fmt_sec(time.perf_counter() - t0)}) - {res.summary}")
        except (StepError, SystemExit, Exception) as e:  # 어떤 오류든 어느 단계인지 알리고 멈춘다
            msg = str(e) or type(e).__name__
            if not isinstance(e, (StepError, SystemExit)):
                msg = f"{type(e).__name__}: {msg}"
            entry.update(status="failed", summary=msg)
            report.update(ok=False, failed_step=name, error=msg)
            out(f"      실패 ({fmt_sec(time.perf_counter() - t0)}) - {msg}")
        entry["sec"] = round(time.perf_counter() - t0, 1)
        report["steps"].append(entry)
        report["warnings"] += [f"({name}) {w}" for w in entry["warnings"]]
        if not report["ok"]:
            break
    report["total_sec"] = round(time.perf_counter() - started, 1)
    return report


def save_report(report: dict, data_dir: str = "data") -> Path:
    path = Path(data_dir) / "run_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
