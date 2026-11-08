"""전체 실행(newscard run) 테스트. 실제 단계는 가짜로 바꿔서 순서/중단/점검 동작만 검증한다."""
import json
import sys
import types

import pytest

from newscard import cli, runner as R
from newscard.models import EventSummary, VerifyResult


def fake_steps(log, fail=None, warn=None, boom=None, exit_=None):
    def make(name):
        def step(ctx):
            log.append(name)
            if name == fail:
                raise R.StepError(f"{name} 사유")
            if name == boom:
                raise KeyError("예상치 못한 오류")
            if name == exit_:
                raise SystemExit("data/x.json 이 없어요")
            return R.StepResult(f"{name} 끝", [f"{name} 경고"] if name == warn else [])
        return step
    return [(n, make(n)) for n in R.STEP_NAMES]


# ---- 단계 선택 ----
def test_select_steps():
    assert R.select_steps(None, None) == R.STEP_NAMES
    assert R.select_steps("script", None) == ["script", "check", "cards", "tts", "video"]
    assert R.select_steps(None, "verify") == ["top", "summarize", "verify"]
    assert R.select_steps("cards", "tts") == ["cards", "tts"]
    assert R.select_steps("tts", "tts") == ["tts"]


def test_select_steps_errors():
    with pytest.raises(ValueError, match="알 수 없는 단계"):
        R.select_steps("없는단계", None)
    with pytest.raises(ValueError, match="뒤에"):
        R.select_steps("video", "top")


def test_step_order_matches_the_pipeline():
    assert R.STEP_NAMES == ["top", "summarize", "verify", "script", "check", "cards", "tts", "video"]


# ---- 실행 ----
def test_runs_all_steps_in_order_and_collects_warnings(monkeypatch):
    log, lines = [], []
    monkeypatch.setattr(R, "STEPS", fake_steps(log, warn="tts"))
    rep = R.run_pipeline(R.Context(), R.STEP_NAMES, out=lines.append)
    assert log == R.STEP_NAMES and rep["ok"] and rep["failed_step"] == ""
    assert [s["status"] for s in rep["steps"]] == ["ok"] * 8
    assert rep["warnings"] == ["(tts) tts 경고"]  # 어느 단계의 경고인지 붙는다
    assert any("[3/8] verify" in x for x in lines)


def test_stops_at_failed_step_and_reports_it(monkeypatch):
    log = []
    monkeypatch.setattr(R, "STEPS", fake_steps(log, fail="script"))
    rep = R.run_pipeline(R.Context(), R.STEP_NAMES, out=lambda s: None)
    assert log == ["top", "summarize", "verify", "script"]  # 뒤 단계는 실행하지 않는다
    assert not rep["ok"] and rep["failed_step"] == "script" and "script 사유" in rep["error"]
    assert [s["status"] for s in rep["steps"]][-1] == "failed" and len(rep["steps"]) == 4


def test_unexpected_exception_is_reported_with_type(monkeypatch):
    monkeypatch.setattr(R, "STEPS", fake_steps([], boom="cards"))
    rep = R.run_pipeline(R.Context(), R.STEP_NAMES, out=lambda s: None)
    assert rep["failed_step"] == "cards" and "KeyError" in rep["error"]


def test_systemexit_from_a_step_does_not_kill_the_runner(monkeypatch):
    monkeypatch.setattr(R, "STEPS", fake_steps([], exit_="tts"))
    rep = R.run_pipeline(R.Context(), R.STEP_NAMES, out=lambda s: None)
    assert rep["failed_step"] == "tts" and "x.json" in rep["error"]


def test_partial_run_and_report_file(monkeypatch, tmp_path):
    log = []
    monkeypatch.setattr(R, "STEPS", fake_steps(log))
    rep = R.run_pipeline(R.Context(), R.select_steps("cards", "tts"), out=lambda s: None)
    assert log == ["cards", "tts"]
    path = R.save_report(rep, str(tmp_path / "data"))
    assert json.loads(path.read_text(encoding="utf-8"))["ok"] is True


def test_fmt_sec():
    assert R.fmt_sec(4.26) == "4.3초" and R.fmt_sec(252) == "4분 12초"


# ---- 실제 단계 함수의 판정 (가짜 하위 모듈) ----
def _install(monkeypatch, module, fn):
    monkeypatch.setattr(module, "run", fn)


def test_step_summarize_fails_when_everything_failed_and_warns_on_title_only(monkeypatch):
    from newscard import summarize

    def s(rank, status="ok", basis="body"):
        return EventSummary(rank=rank, event_title="t", event_url="u", status=status, basis=basis, error="연결 오류" if status != "ok" else "")

    _install(monkeypatch, summarize, lambda: [s(1, "failed"), s(2, "failed")])
    with pytest.raises(R.StepError, match="Ollama"):
        R.step_summarize(R.Context())
    _install(monkeypatch, summarize, lambda: [s(1), s(2, basis="title_only"), s(3, "failed")])
    res = R.step_summarize(R.Context())
    assert "2/3" in res.summary and len(res.warnings) == 2


def test_step_verify_strict_stops_only_when_flagged(monkeypatch):
    from newscard import verify

    flagged = VerifyResult(rank=1, status="flag", rule_issues=["숫자 '5,000'이(가) 본문에 없음"])
    ok = VerifyResult(rank=2, status="pass")
    _install(monkeypatch, verify, lambda: [flagged, ok])
    res = R.step_verify(R.Context(strict=False))  # 기본은 계속 진행하고 경고로 남긴다
    assert "검토 필요 1" in res.summary and "5,000" in res.warnings[0]
    with pytest.raises(R.StepError, match="--strict"):
        R.step_verify(R.Context(strict=True))
    _install(monkeypatch, verify, lambda: [ok])
    assert R.step_verify(R.Context(strict=True)).warnings == []


def test_step_script_and_tts_failure_rules(monkeypatch):
    from newscard import script, tts

    _install(monkeypatch, script, lambda: [])
    with pytest.raises(R.StepError, match="대본"):
        R.step_script(R.Context())
    _install(monkeypatch, tts, lambda: [{"index": 0, "status": "failed", "error": "엔진 오류", "duration_sec": 0, "warnings": []}])
    with pytest.raises(R.StepError, match="하나도"):
        R.step_tts(R.Context())
    items = [{"index": 0, "status": "ok", "error": "", "duration_sec": 5.0, "warnings": ["영어 'Tesla'..."]},
             {"index": 1, "status": "failed", "error": "x", "duration_sec": 0, "warnings": []}]
    _install(monkeypatch, tts, lambda: items)
    res = R.step_tts(R.Context())
    assert "1/2" in res.summary and len(res.warnings) == 2


# ---- 사전 점검 ----
def fake_ollama(models=("qwen2.5:3b",), fail=False):
    mod = types.ModuleType("ollama")

    class Client:
        def __init__(self, host=None, timeout=None):
            pass

        def list(self):
            if fail:
                raise ConnectionError("refused")
            return {"models": [{"model": m} for m in models]}

    mod.Client = Client
    return mod


def test_preflight_all_good(monkeypatch):
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama())
    monkeypatch.setattr(R.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(R.importlib.util, "find_spec", lambda n: object())
    from newscard import cards
    monkeypatch.setattr(cards, "find_fonts", lambda: ("a", "b"))
    assert R.preflight(R.STEP_NAMES) == []


def test_preflight_reports_every_problem_at_once(monkeypatch):
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama(fail=True))
    monkeypatch.setattr(R.shutil, "which", lambda n: None)
    monkeypatch.setattr(R.importlib.util, "find_spec", lambda n: None)
    from newscard import cards

    def no_font():
        raise cards.FontNotFound("한글 폰트를 찾지 못했어요")

    monkeypatch.setattr(cards, "find_fonts", no_font)
    problems = R.preflight(R.STEP_NAMES)
    text = "\n".join(problems)
    assert len(problems) == 4
    for key in ("Ollama", "한글 폰트", "FFmpeg", "Piper"):
        assert key in text


def test_preflight_model_missing_and_only_needed_steps_checked(monkeypatch):
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama(models=("llama3.2:3b",)))
    p = R.preflight(["summarize"])
    assert len(p) == 1 and "qwen2.5:3b" in p[0] and "ollama pull" in p[0]
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama(fail=True))
    assert R.preflight(["verify", "check"]) == []  # Ollama가 필요 없는 단계만이면 점검하지 않는다


def test_preflight_accepts_model_without_tag_match(monkeypatch):
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama(models=("qwen2.5:3b",)))
    monkeypatch.setenv("NEWSCARD_MODEL", "qwen2.5:3b")
    assert R.preflight(["script"]) == []


# ---- 명령어 ----
def run_cli(monkeypatch, capsys, argv, steps, problems=()):
    monkeypatch.setattr(R, "STEPS", steps)
    monkeypatch.setattr(R, "preflight", lambda names: list(problems))
    monkeypatch.setattr(R, "save_report", lambda rep, d="data": "data/run_report.json")
    monkeypatch.setattr(sys, "argv", ["newscard", "run", *argv])
    code = 0
    try:
        cli.cmd_run()
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr().out


def test_cli_success(monkeypatch, capsys):
    code, out = run_cli(monkeypatch, capsys, [], fake_steps([], warn="verify"))
    assert code == 0 and "모든 단계 완료" in out and "확인이 필요한 항목 1건" in out and "(verify) verify 경고" in out


def test_cli_failure_tells_how_to_resume(monkeypatch, capsys):
    code, out = run_cli(monkeypatch, capsys, [], fake_steps([], fail="tts"))
    assert code == 1 and "'tts' 단계에서 멈췄어요" in out and "newscard run --from tts" in out


def test_cli_preflight_blocks_run(monkeypatch, capsys):
    log = []
    code, out = run_cli(monkeypatch, capsys, [], fake_steps(log), problems=["Ollama에 연결하지 못했어요"])
    assert code == 1 and log == [] and "Ollama에 연결하지 못했어요" in out  # 아무 단계도 실행하지 않는다


def test_cli_from_until_theme_and_bad_arguments(monkeypatch, capsys):
    log = []
    code, out = run_cli(monkeypatch, capsys, ["--from", "cards", "--until", "tts", "--theme", "dark"], fake_steps(log))
    assert code == 0 and log == ["cards", "tts"] and "cards -> tts" in out
    code, out = run_cli(monkeypatch, capsys, ["--from", "xx"], fake_steps([]))
    assert code != 0
