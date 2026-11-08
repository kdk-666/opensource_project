"""요약이 본문에 근거하는지 두 단계로 검토한다.

1) 코드 검사 (즉시, 모델 없음): 요약의 숫자, 인용문("..."), 영문 이름이 본문에 실제로 있는지 글자로 대조한다.
2) LLM 검토 (요약과 다른 모델): 요약을 주장 단위로 나누고 각 주장의 근거 문장을 본문에서 복사해 오게 한다.
   모델이 적은 근거 문장이 본문에 정말 있는지는 코드가 다시 확인한다. (작은 모델이 근거를 꾸며내는 것을 막는다)

검토만 하고 요약을 고치거나 지우지 않는다. 결과는 data/verification.json에 따로 저장한다.
"""
import json
import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from newscard.llm import ask_json
from newscard.models import EventSummary, TopEvent, VerifyResult
from newscard.summarize import DEFAULT_MODEL, MAX_BODY_CHARS, make_client, pick_num_ctx

log = logging.getLogger(__name__)

PROMPT_PATH = Path("prompts/verify.txt")
DEFAULT_VERIFY_MODEL = "llama3.2:3b"
FAKE_CLAIM = "정부는 이번 일과 관련해 책임자 전원을 해임하기로 결정했다고 밝혔다."  # 자체 점검용 가짜 주장

_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([천만억조]?)")
_MULT = {"": 1, "천": 1e3, "만": 1e4, "억": 1e8, "조": 1e12}
_QUOTE = re.compile(r"[“\"‘']([^”\"’']{4,})[”\"’']")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9&.\-]{1,}")


def _squash(s: str) -> str:
    """공백, 따옴표, 문장부호를 지워 글자만 남긴다."""
    return re.sub(r"[\W_]+", "", s).casefold()


def _numbers(text: str) -> tuple[set[str], set[float]]:
    raw, vals = set(), set()
    for num, unit in _NUM.findall(text):
        n = num.replace(",", "")
        try:
            v = float(n)
        except ValueError:
            continue
        raw.add(n.rstrip("0").rstrip(".") if "." in n else n)
        vals.add(round(v * _MULT[unit], 4))
    return raw, vals


def rule_check(summary_text: str, article_text: str) -> list[str]:
    """요약에 있는데 본문에 없는 숫자, 인용문, 영문 이름을 찾는다. 문제가 없으면 빈 목록."""
    issues = []
    t_raw, t_vals = _numbers(article_text)
    for num, unit in _NUM.findall(summary_text):
        n = num.replace(",", "")
        try:
            v = round(float(n) * _MULT[unit], 4)
        except ValueError:
            continue
        key = n.rstrip("0").rstrip(".") if "." in n else n
        if v not in t_vals and key not in t_raw:
            issues.append(f"숫자 '{num}{unit}'이(가) 본문에 없음")
    squashed = _squash(article_text)
    for q in _QUOTE.findall(summary_text):
        if _squash(q) not in squashed:
            issues.append(f"인용문 '{q[:20]}'이(가) 본문에 없음")
    lower = article_text.casefold()
    for w in dict.fromkeys(_LATIN.findall(summary_text)):
        if w.casefold() not in lower:
            issues.append(f"영문 '{w}'이(가) 본문에 없음")
    return list(dict.fromkeys(issues))


class Claim(BaseModel):
    text: str
    supported: bool
    evidence: str = ""


class Judge(BaseModel):
    claims: list[Claim] = Field(min_length=1)


def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)}


def evidence_found(evidence: str, article_text: str, threshold: float = 0.7) -> bool:
    """모델이 적은 근거 문장이 본문에 실제로 있는지. 글자 2개 조각의 70% 이상이 본문에 있으면 인정한다."""
    e, a = _squash(evidence), _squash(article_text)
    if len(e) < 6:
        return False
    if e in a:
        return True
    grams = _bigrams(e)
    return bool(grams) and len(grams & _bigrams(a)) / len(grams) >= threshold


def judge_claims(judge: Judge, article_text: str) -> list[dict]:
    out = []
    for c in judge.claims:
        if not c.supported:
            verdict = "unsupported"
        elif evidence_found(c.evidence, article_text):
            verdict = "supported"
        else:
            verdict = "unverified"  # 모델은 근거가 있다고 했지만 그 문장을 본문에서 못 찾음
        out.append({"text": c.text, "supported": c.supported, "evidence": c.evidence, "verdict": verdict})
    return out


def summary_text(s: EventSummary) -> str:
    return " ".join([s.headline, s.summary, *s.key_points])


def source_text(event: TopEvent | None, s: EventSummary) -> str:
    """요약에 실제로 쓴 기사의 본문(없으면 제목)."""
    if event is not None and s.sources:
        url = s.sources[0].get("url")
        for a in event.articles:
            if a.url == url:
                return (a.text[:MAX_BODY_CHARS] if a.text else a.title)
    return ""


def build_message(prompt: str, s: EventSummary, article: str) -> str:
    return (
        prompt.replace("{article}", article)
        .replace("{headline}", s.headline)
        .replace("{summary}", s.summary)
        .replace("{points}", "\n".join(f"- {p}" for p in s.key_points))
    )


def verify_one(s: EventSummary, article: str, client, model: str, prompt: str,
               use_llm: bool = True) -> VerifyResult:
    r = VerifyResult(rank=s.rank, headline=s.headline, status="pass", basis=s.basis, model=model)
    if s.status != "ok":
        r.status, r.notes = "skipped", ["요약에 실패한 사건이라 검토하지 않음"]
        return r
    if not article:
        r.status, r.notes = "skipped", ["요약에 쓴 기사 본문을 찾지 못해 검토하지 않음"]
        return r

    r.rule_issues = rule_check(summary_text(s), article)

    if s.basis == "title_only":
        r.llm_status = "skipped"
        r.notes.append("제목만으로 만든 요약이라 LLM 검토는 하지 않음 (검토할 본문이 없음)")
    elif use_llm:
        judge, attempts, error, elapsed = ask_json(
            client, model, build_message(prompt, s, article), Judge,
            num_ctx=pick_num_ctx(len(article) + len(summary_text(s)) + 1500),
        )
        r.attempts, r.elapsed_sec = attempts, elapsed
        if judge is None:
            r.llm_status = "failed"
            r.notes.append(f"LLM 검토 실패: {error}")
        else:
            r.llm_status = "ok"
            r.claims = judge_claims(judge, article)

    verdicts = [c["verdict"] for c in r.claims]
    if r.rule_issues or "unsupported" in verdicts:
        r.status = "flag"
    elif "unverified" in verdicts or r.llm_status == "failed":
        r.status = "warn"
    return r


def verify_all(summaries: list[EventSummary], events: list[TopEvent], client, model: str,
               prompt: str, use_llm: bool = True) -> list[VerifyResult]:
    by_rank = {e.rank: e for e in events}
    out = []
    for s in sorted(summaries, key=lambda x: x.rank):
        log.info("[%d위] 검토 중: %s", s.rank, s.headline[:30])
        out.append(verify_one(s, source_text(by_rank.get(s.rank), s), client, model, prompt, use_llm=use_llm))
    return out


def selftest(summaries: list[EventSummary], events: list[TopEvent], client, model: str, prompt: str) -> dict:
    """검토 모델이 쓸 만한지 점검한다. 원본 요약과, 가짜 주장 한 문장을 끼운 요약에 같은 검토를 돌려서
    가짜를 얼마나 잡고(적발), 멀쩡한 요약을 얼마나 잘못 의심하는지(오탐) 센다. LLM 검토만 본다."""
    by_rank = {e.rank: e for e in events}
    caught = false_alarm = n = 0
    rows = []
    for s in sorted(summaries, key=lambda x: x.rank):
        article = source_text(by_rank.get(s.rank), s)
        if s.status != "ok" or s.basis == "title_only" or not article:
            continue
        fake = s.model_copy(update={"summary": f"{s.summary} {FAKE_CLAIM}"})
        orig = verify_one(s, article, client, model, prompt)
        bad = verify_one(fake, article, client, model, prompt)
        n += 1
        o_flag = any(c["verdict"] == "unsupported" for c in orig.claims)
        b_flag = any(c["verdict"] == "unsupported" for c in bad.claims)
        caught += b_flag
        false_alarm += o_flag
        rows.append({"rank": s.rank, "원본_오탐": o_flag, "가짜_적발": b_flag,
                     "llm": f"{orig.llm_status}/{bad.llm_status}"})
    return {"사건수": n, "가짜_적발": caught, "원본_오탐": false_alarm, "상세": rows}


def _load(data_dir: str):
    d = Path(data_dir)
    for name, hint in (("summaries.json", "newscard summarize"), ("top_events.json", "newscard")):
        if not (d / name).exists():
            raise SystemExit(f"data/{name} 이 없어요. 먼저 `{hint}`를 실행하세요.")
    summaries = [EventSummary.model_validate(x) for x in json.loads((d / "summaries.json").read_text(encoding="utf-8"))]
    events = [TopEvent.model_validate(x) for x in json.loads((d / "top_events.json").read_text(encoding="utf-8"))]
    return summaries, events


def verify_model() -> str:
    model = os.environ.get("NEWSCARD_VERIFY_MODEL", DEFAULT_VERIFY_MODEL)
    if model == os.environ.get("NEWSCARD_MODEL", DEFAULT_MODEL):
        log.warning("검토 모델이 요약 모델과 같습니다(%s). 다른 모델을 쓰는 편이 좋아요.", model)
    return model


def run(data_dir: str = "data", use_llm: bool = False) -> list[VerifyResult]:
    """기본은 코드 검사만(모델 없이 즉시). use_llm=True면 LLM 검토까지 한다."""
    summaries, events = _load(data_dir)
    if use_llm:
        results = verify_all(summaries, events, make_client(), verify_model(),
                             PROMPT_PATH.read_text(encoding="utf-8"))
    else:
        results = verify_all(summaries, events, None, "", "", use_llm=False)
    (Path(data_dir) / "verification.json").write_text(
        json.dumps([r.model_dump() for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def run_selftest(data_dir: str = "data") -> dict:
    summaries, events = _load(data_dir)
    return selftest(summaries, events, make_client(), verify_model(), PROMPT_PATH.read_text(encoding="utf-8"))
