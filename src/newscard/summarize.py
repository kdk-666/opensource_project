"""Top 사건의 대표 기사 본문을 Ollama로 요약한다. 자료에 없는 내용은 쓰지 않도록 프롬프트로 제한하고,
JSON 형식이 틀리면 다시 시도한다. 한 사건이 실패해도 나머지는 계속 처리한다."""
import json
import logging
import os
import time
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from newscard.models import EventSummary, TopEvent

log = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5:3b"
PROMPT_PATH = Path("prompts/summarize.txt")


class LLMOutput(BaseModel):
    """LLM이 돌려줘야 하는 JSON 형태. 어긋나면 다시 시도한다."""

    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    key_points: list[str] = Field(min_length=1)

    @field_validator("headline", "summary")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


MAX_BODY_CHARS = 12000  # 이보다 긴 본문만 잘라서 준다 (대부분의 기사는 그대로 전체)


def pick_representative(event: TopEvent):
    """사건의 대표 기사."""
    for a in event.articles:
        if a.url == event.url:
            return a
    return event.articles[0]


def choose_article(event: TopEvent):
    """요약에 쓸 기사와 근거 종류를 정한다.

    1) 대표 기사 본문이 있으면 대표 기사 (body)
    2) 없으면 같은 사건으로 확인된 다른 기사 중 본문이 있는 첫 기사 (body_fallback)
       - 다른 대표 기사(RSS 항목) -> 관련 기사 순서
    3) 모두 없으면 대표 기사 제목만 (title_only)
    """
    rep = pick_representative(event)
    if rep.text_status == "ok" and rep.text:
        return rep, "body"
    others = [a for a in event.articles if a is not rep and a.text_status == "ok" and a.text]
    others.sort(key=lambda a: (a.kind != "representative", a.rank or 10**6))
    if others:
        return others[0], "body_fallback"
    return rep, "title_only"


def build_material(event: TopEvent, max_chars: int = MAX_BODY_CHARS) -> tuple[str, str, bool]:
    """모델에 줄 자료를 만든다. (자료, 근거 종류, 잘렸는지)

    기사 한 건의 본문 전체만 준다. 대표 기사 본문이 없을 때만 다른 기사로 대체한다.
    """
    art, basis = choose_article(event)
    head = f"{art.source or '언론사 미상'} - {art.title}"
    if basis == "title_only":
        return head, basis, False
    return f"{head}\n\n{art.text[:max_chars]}", basis, len(art.text) > max_chars


def pick_num_ctx(prompt_chars: int) -> int:
    """입력 길이에 맞는 컨텍스트 크기. 한국어는 보수적으로 1글자 = 1토큰으로 잡고 답변 여유를 더한다."""
    need = prompt_chars + 1500
    return min(max(4096, -(-need // 1024) * 1024), 16384)


def load_prompt(path: Path = PROMPT_PATH) -> str:
    return path.read_text(encoding="utf-8")


def make_client():
    import ollama

    return ollama.Client(host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"), timeout=300)


def _metric(resp, key):
    """Ollama 응답에서 통계값을 꺼낸다. 없으면 None."""
    try:
        return resp.get(key)
    except Exception:
        return None


def summarize_event(event: TopEvent, client, model: str, prompt: str, retries: int = 2) -> EventSummary:
    material, basis, truncated = build_material(event)
    used, _ = choose_article(event)  # 요약에 실제로 쓴 기사
    others = []
    for a in event.articles:
        name = a.source.strip()
        if a is not used and name and name != used.source.strip() and name not in others:
            others.append(name)
    result = EventSummary(
        rank=event.rank,
        event_title=event.title,
        event_url=event.url,
        sources=[{"source": used.source, "title": used.title, "url": used.url}],
        also_reported_by=others,
        basis=basis,
        input_chars=len(material),
        truncated=truncated,
        model=model,
    )
    message = prompt.replace("{material}", material)
    num_ctx = pick_num_ctx(len(message))
    last_error = ""
    started = time.perf_counter()
    for attempt in range(1, retries + 2):
        result.attempts = attempt
        try:
            resp = client.chat(
                model=model,
                messages=[{"role": "user", "content": message}],
                format="json",
                options={"temperature": 0.2, "num_ctx": num_ctx},
            )
            out = LLMOutput.model_validate(json.loads(resp["message"]["content"]))
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as e:
            last_error = f"응답 형식 오류: {type(e).__name__}"
            log.warning("[%d위] 시도 %d 실패: %s", event.rank, attempt, last_error)
            continue
        except Exception as e:  # 연결 끊김, 모델 없음 등
            last_error = f"Ollama 호출 오류: {type(e).__name__}: {e}"
            log.warning("[%d위] 시도 %d 실패: %s", event.rank, attempt, last_error)
            continue
        result.headline, result.summary = out.headline, out.summary
        result.key_points = [p.strip() for p in out.key_points if p.strip()][:3]
        result.prompt_tokens = _metric(resp, "prompt_eval_count")
        result.output_tokens = _metric(resp, "eval_count")
        load = _metric(resp, "load_duration")
        result.load_sec = round(load / 1e9, 2) if isinstance(load, (int, float)) else None
        result.elapsed_sec = round(time.perf_counter() - started, 2)
        log.info("[%d위] 요약 완료 %.1f초 (입력 %s토큰, 출력 %s토큰, 시도 %d회)",
                 event.rank, result.elapsed_sec, result.prompt_tokens, result.output_tokens, attempt)
        return result
    result.status, result.error = "failed", last_error
    result.elapsed_sec = round(time.perf_counter() - started, 2)
    log.warning("[%d위] 요약 실패 %.1f초 (시도 %d회)", event.rank, result.elapsed_sec, result.attempts)
    return result


def summarize_all(events: list[TopEvent], client=None, model: str | None = None) -> list[EventSummary]:
    model = model or os.environ.get("NEWSCARD_MODEL", DEFAULT_MODEL)
    client = client or make_client()
    prompt = load_prompt()
    out = []
    for e in events:
        log.info("[%d위] 요약 중: %s", e.rank, e.title[:30])
        out.append(summarize_event(e, client, model, prompt))
    return out


def run(data_dir: str = "data") -> list[EventSummary]:
    src = Path(data_dir) / "top_events.json"
    if not src.exists():
        raise SystemExit("data/top_events.json 이 없어요. 먼저 `newscard`(Top 5 선정)를 실행하세요.")
    events = [TopEvent.model_validate(x) for x in json.loads(src.read_text(encoding="utf-8"))]
    summaries = summarize_all(events)
    dst = Path(data_dir) / "summaries.json"
    dst.write_text(
        json.dumps([s.model_dump() for s in summaries], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summaries
