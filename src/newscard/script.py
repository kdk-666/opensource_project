"""요약(summaries.json)을 카드뉴스 대본(script.json)으로 만든다.

구성: 인트로 1장 + 사건별 카드 + 아웃트로 1장.
- 인트로/아웃트로는 코드의 고정 문장(모델 사용 안 함).
- 사건 카드의 제목과 짧은 줄은 요약 결과(headline, key_points)를 그대로 쓴다.
- 나레이션만 Ollama가 요약을 말하는 문체로 다시 쓴다. 코드가 검사해서(문장 끝맺음, 요약에 없는 숫자/단어)
  통과하지 못하면 다시 쓰게 한다.
- 3번 시도해도 통과하지 못하면 요약문을 문장 단위로 잘라 나레이션으로 대체하고 generated_by="fallback"으로 표시한다.
- 요약에 실패한 사건은 대본에서 제외한다.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from newscard.cluster import tokens
from newscard.llm import ask_json
from newscard.models import EventSummary, ScriptCard
from newscard.summarize import DEFAULT_MODEL, make_client
from newscard.verify import rule_check

log = logging.getLogger(__name__)

PROMPT_PATH = Path("prompts/script.txt")
MAX_NARRATION = 140  # 글자. 읽는 속도 약 5.5자/초 기준 약 25초
MIN_NARRATION = 30
MIN_GROUNDING = 0.45  # 나레이션 단어 중 요약에 있는 단어의 비율이 이보다 낮으면 지어낸 내용으로 의심
MAX_TRIES = 3
CHARS_PER_SEC = 5.5  # 한국어 낭독 속도 추정치. 실제 TTS 음성 길이와 다를 수 있다.
KST = timezone(timedelta(hours=9))


class Narration(BaseModel):
    """LLM이 돌려줘야 하는 JSON. 나레이션 한 문단만 쓰게 한다. (제목/줄은 요약 결과를 그대로 쓴다)"""

    narration: str = Field(min_length=1)  # 길이는 스키마가 아니라 아래 fit_to_limit/check_length에서 다룬다

    @field_validator("narration")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s*", text.strip()) if s.strip()]


def fit_to_limit(narration: str, limit: int = MAX_NARRATION) -> str:
    """너무 길면 뒤의 문장부터 빼서 limit 안에 맞춘다. 문장을 중간에서 자르지 않는다.
    빼고 나면 너무 짧아지는 경우에는 원문을 그대로 돌려준다(검사에서 거절된다)."""
    narration = narration.strip()
    if len(narration) <= limit:
        return narration
    out = ""
    for sent in _sentences(narration):
        if len(f"{out} {sent}".strip()) > limit:
            break
        out = f"{out} {sent}".strip()
    return out if len(out) >= MIN_NARRATION else narration


def check_length(narration: str) -> str:
    if len(narration) > MAX_NARRATION:
        return f"나레이션이 {len(narration)}자로 {MAX_NARRATION}자를 넘음"
    if len(narration) < MIN_NARRATION:
        return f"나레이션이 {len(narration)}자로 너무 짧음 (최소 {MIN_NARRATION}자)"
    return ""


def grounding(narration: str, summary_text: str) -> float:
    """나레이션의 핵심 단어 중 요약에 실제로 있는 비율 (0~1)."""
    n = set(tokens(narration))
    return len(n & set(tokens(summary_text))) / len(n) if n else 0.0


def check_narration(narration: str, s: EventSummary) -> str:
    """나레이션이 쓸 만한지 코드로 검사한다. 문제가 없으면 빈 글자, 있으면 사유."""
    if re.search(r"[()\[\]{}<>]|https?://", narration):
        return "괄호/기호/링크가 들어 있음"
    sents = _sentences(narration)
    if not sents or not all(x.rstrip('.!?"”’\' ').endswith("다") for x in sents):
        return "단어만 나열했거나 문장이 '~다'로 끝나지 않음"
    source = " ".join([s.headline, s.summary, *s.key_points])
    issues = rule_check(narration, source)  # 요약에 없는 숫자/인용문/영문
    if issues:
        return "요약에 없는 내용: " + "; ".join(issues[:2])
    g = grounding(narration, source)
    if g < MIN_GROUNDING:
        return f"요약에 없는 단어가 많음(일치율 {g:.2f} < {MIN_GROUNDING})"
    return ""


def est_seconds(text: str) -> float:
    return round(len(re.sub(r"\s", "", text)) / CHARS_PER_SEC, 1)


def fallback_narration(summary: str, headline: str, limit: int = MAX_NARRATION) -> str:
    """모델 실패 시: 요약문을 문장 단위로 limit 안에서 이어 붙인다. 하나도 안 들어가면 제목만."""
    sentences = [s.strip() for s in re.split(r"(?<=[.다요])\s+", summary.strip()) if s.strip()]
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > limit:
            break
        out = f"{out} {s}".strip()
    return out or headline[:limit]


def source_line(s: EventSummary) -> str:
    if not s.sources:
        return ""
    name = s.sources[0].get("source") or "언론사 미상"
    line = f"출처: {name}"
    if s.also_reported_by:
        line += f" 외 {len(s.also_reported_by)}곳 보도"
    return line


def make_news_card(index: int, s: EventSummary, client, model: str, prompt: str) -> ScriptCard:
    """제목과 짧은 줄은 요약 결과를 그대로 쓰고, 나레이션만 모델이 말하는 문체로 다시 쓴다."""
    message = (
        prompt.replace("{max_narration}", str(MAX_NARRATION))
        .replace("{headline}", s.headline)
        .replace("{summary}", s.summary)
        .replace("{points}", "\n".join(f"- {p}" for p in s.key_points))
    )
    card = ScriptCard(
        index=index, kind="news", rank=s.rank, title=s.headline, bullets=s.key_points[:3],
        narration="", source_line=source_line(s), basis=s.basis, model=model,
    )
    reasons = []
    ask = message
    for attempt in range(1, MAX_TRIES + 1):
        out, calls, error, elapsed = ask_json(client, model, ask, Narration, retries=0)
        card.attempts += calls
        card.elapsed_sec = round(card.elapsed_sec + elapsed, 2)
        if out is None:
            reasons.append(error)
            problem = error
        else:
            text = fit_to_limit(out.narration)
            problem = check_length(text) or check_narration(text, s)
            if not problem:
                card.narration, card.generated_by = text, "llm"
                break
            reasons.append(problem)
            log.warning("[%d위] 나레이션 거절(%d/%d): %s", s.rank, attempt, MAX_TRIES, problem)
        # 다음 시도에는 거절 사유를 알려 준다
        ask = f"{message}\n\n[이전 답변이 거절된 이유] {problem}\n이유를 고쳐서 JSON으로 다시 답해."
    else:
        card.narration = fallback_narration(s.summary, s.headline)
        card.generated_by, card.error = "fallback", " / ".join(reasons)
        log.warning("[%d위] 나레이션 생성 실패 -> 요약문으로 대체: %s", s.rank, card.error)
    card.est_sec = est_seconds(card.narration)
    return card


def make_intro(today: datetime, n: int) -> ScriptCard:
    text = f"{today.month}월 {today.day}일, 오늘의 주요 뉴스 {n}가지를 전해드립니다."
    return ScriptCard(index=0, kind="intro", title=f"오늘의 주요 뉴스 Top {n}",
                      bullets=[f"{today.year}년 {today.month}월 {today.day}일"], narration=text,
                      est_sec=est_seconds(text))


def make_outro(index: int) -> ScriptCard:
    text = "이상 오늘의 주요 뉴스였습니다. 이 영상은 기사를 인공지능이 요약해 만들었습니다."
    return ScriptCard(index=index, kind="outro", title="시청해 주셔서 감사합니다",
                      bullets=["기사 요약은 인공지능이 작성했습니다", "출처는 각 카드에 표기했습니다"],
                      narration=text, est_sec=est_seconds(text))


def build_script(summaries: list[EventSummary], client, model: str, prompt: str,
                 today: datetime | None = None) -> list[ScriptCard]:
    today = today or datetime.now(KST)
    ok = sorted((s for s in summaries if s.status == "ok"), key=lambda s: s.rank)
    for s in summaries:
        if s.status != "ok":
            log.warning("[%d위] 요약 실패 사건은 대본에서 제외: %s", s.rank, s.event_title[:30])
    if not ok:
        return []
    cards = [make_intro(today, len(ok))]
    for s in ok:
        log.info("[%d위] 대본 작성 중: %s", s.rank, s.headline[:30])
        cards.append(make_news_card(len(cards), s, client, model, prompt))
    cards.append(make_outro(len(cards)))
    return cards


def run(data_dir: str = "data") -> list[ScriptCard]:
    src = Path(data_dir) / "summaries.json"
    if not src.exists():
        raise SystemExit("data/summaries.json 이 없어요. 먼저 `newscard summarize`를 실행하세요.")
    summaries = [EventSummary.model_validate(x) for x in json.loads(src.read_text(encoding="utf-8"))]
    model = os.environ.get("NEWSCARD_MODEL", DEFAULT_MODEL)
    cards = build_script(summaries, make_client() if summaries else None, model,
                         PROMPT_PATH.read_text(encoding="utf-8"))
    (Path(data_dir) / "script.json").write_text(
        json.dumps([c.model_dump() for c in cards], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return cards
