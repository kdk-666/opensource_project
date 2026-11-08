"""대본(script.json)을 모델 없이 코드로 검사한다. 대본을 고치거나 지우지 않고 결과만 기록한다.

카드마다 보는 것:
- 나레이션 길이, 끝맺음(~다), 괄호/기호/링크
- 나레이션이 요약에 근거하는지 (요약에 없는 숫자/인용문/영문, 단어 일치율)
- 나레이션의 숫자/인용문/영문이 요약에 쓴 원문 기사 본문에도 있는지
- 제목과 짧은 줄이 요약 결과 그대로인지, 출처 표기가 있는지
- 요약문으로 대체됐거나(fallback) 제목만으로 만든 카드인지
전체 구조: 인트로 → 사건 카드 → 아웃트로 순서, 번호, 인트로의 사건 수, 영상 길이 추정.
"""
import json
import re
from pathlib import Path

from newscard.models import EventSummary, ScriptCard, ScriptCheck, TopEvent
from newscard.script import MAX_NARRATION, MIN_NARRATION, check_narration, est_seconds, source_line
from newscard.verify import rule_check, source_text

MAX_CARD_SEC = 30.0  # 카드 한 장 나레이션이 이보다 길면 알림 (글자 수 기준 추정)
MAX_VIDEO_SEC = 150.0  # 영상 전체가 이보다 길면 알림 (숏폼 기준 추정치, 팀 기준에 맞게 조정)


def check_news_card(card: ScriptCard, s: EventSummary | None, article: str) -> ScriptCheck:
    r = ScriptCheck(index=card.index, kind=card.kind, rank=card.rank, status="pass",
                    est_sec=est_seconds(card.narration))
    n = card.narration.strip()

    # 길이
    if not n:
        r.issues.append("나레이션이 비어 있음")
    elif len(n) > MAX_NARRATION:
        r.issues.append(f"나레이션이 {len(n)}자로 최대 {MAX_NARRATION}자를 넘음")
    elif len(n) < MIN_NARRATION:
        r.issues.append(f"나레이션이 {len(n)}자로 너무 짧음 (최소 {MIN_NARRATION}자)")
    if r.est_sec > MAX_CARD_SEC:
        r.warnings.append(f"나레이션 추정 {r.est_sec}초 (기준 {MAX_CARD_SEC:.0f}초)")

    if s is None:
        r.warnings.append("대응하는 요약을 찾지 못해 요약 대조는 하지 못함")
    elif n:
        problem = check_narration(n, s)  # 끝맺음, 기호, 요약에 없는 숫자/단어
        if problem:
            r.issues.append(problem)
        # 제목/줄은 요약 결과 그대로여야 한다
        if card.title != s.headline:
            r.warnings.append("카드 제목이 요약 제목과 다름")
        if any(b not in s.key_points for b in card.bullets):
            r.warnings.append("카드의 짧은 줄이 요약 핵심과 다름")
        if card.source_line != source_line(s):
            r.warnings.append("출처 표기가 요약의 출처와 다름")
        if s.basis == "title_only":
            r.warnings.append("제목만으로 만든 요약에서 나온 카드 (내용이 제한적)")
        # 원문 본문 대조
        if article and s.basis != "title_only":
            for i in rule_check(n, article):
                r.issues.append(f"원문 기사 대조: {i}")
    if not card.source_line:
        r.warnings.append("출처 표기가 비어 있음")
    if card.generated_by == "fallback":
        r.warnings.append("모델이 쓰지 못해 요약문으로 대체한 나레이션")

    r.status = "flag" if r.issues else ("warn" if r.warnings else "pass")
    return r


def check_fixed_card(card: ScriptCard, n_news: int) -> ScriptCheck:
    r = ScriptCheck(index=card.index, kind=card.kind, status="pass", est_sec=est_seconds(card.narration))
    n = card.narration.strip()
    if not n:
        r.issues.append("나레이션이 비어 있음")
    if re.search(r"[()\[\]{}<>]|https?://", n):
        r.issues.append("괄호/기호/링크가 들어 있음")
    if card.kind == "intro":
        m = re.search(r"(\d+)가지", n)
        if not m or int(m.group(1)) != n_news:
            r.issues.append(f"인트로의 사건 수가 실제 사건 카드 수({n_news})와 다름")
    if card.kind == "outro" and "인공지능" not in n:
        r.warnings.append("아웃트로에 인공지능 요약 고지가 없음")
    r.status = "flag" if r.issues else ("warn" if r.warnings else "pass")
    return r


def check_script(cards: list[ScriptCard], summaries: list[EventSummary], events: list[TopEvent]) -> dict:
    by_rank_s = {s.rank: s for s in summaries}
    by_rank_e = {e.rank: e for e in events}
    structure: list[str] = []

    if not cards:
        return {"structure_issues": ["대본이 비어 있음"], "total_est_sec": 0.0, "cards": []}

    if cards[0].kind != "intro":
        structure.append("첫 카드가 인트로가 아님")
    if cards[-1].kind != "outro":
        structure.append("마지막 카드가 아웃트로가 아님")
    if [c.index for c in cards] != list(range(len(cards))):
        structure.append("카드 번호(index)가 0부터 순서대로가 아님")
    news = [c for c in cards if c.kind == "news"]
    ranks = [c.rank for c in news]
    if ranks != sorted(ranks) or len(set(ranks)) != len(ranks):
        structure.append("사건 카드의 순위가 순서대로가 아니거나 중복됨")
    ok_ranks = sorted(s.rank for s in summaries if s.status == "ok")
    missing = [r for r in ok_ranks if r not in ranks]
    if missing:
        structure.append(f"요약에 성공한 사건 {missing}위가 대본에 없음")

    results = []
    for c in cards:
        if c.kind == "news":
            s = by_rank_s.get(c.rank)
            article = source_text(by_rank_e.get(c.rank), s) if s else ""
            results.append(check_news_card(c, s, article))
        else:
            results.append(check_fixed_card(c, len(news)))

    total = round(sum(r.est_sec for r in results), 1)
    if total > MAX_VIDEO_SEC:
        structure.append(f"영상 길이 추정 {total:.0f}초 (기준 {MAX_VIDEO_SEC:.0f}초 초과)")
    return {"structure_issues": structure, "total_est_sec": total, "cards": [r.model_dump() for r in results]}


def run(data_dir: str = "data") -> dict:
    d = Path(data_dir)
    for name, hint in (("script.json", "newscard script"), ("summaries.json", "newscard summarize"),
                       ("top_events.json", "newscard")):
        if not (d / name).exists():
            raise SystemExit(f"data/{name} 이 없어요. 먼저 `{hint}`를 실행하세요.")
    load = lambda n: json.loads((d / n).read_text(encoding="utf-8"))  # noqa: E731
    report = check_script([ScriptCard.model_validate(x) for x in load("script.json")],
                          [EventSummary.model_validate(x) for x in load("summaries.json")],
                          [TopEvent.model_validate(x) for x in load("top_events.json")])
    (d / "script_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
