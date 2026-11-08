"""Ollama 없이 가짜 클라이언트로 요약 검토를 검증한다."""
import json

import pytest

from newscard.models import Article, EventSummary, TopEvent
from newscard.verify import (FAKE_CLAIM, summary_text as summary_text_of, evidence_found, rule_check, selftest, verify_all,
                             verify_one)

ARTICLE = (
    "서울에너지공사는 7일 새벽 마곡동 일대 열수송관이 파열돼 약 9천 세대의 난방과 온수 공급이 중단됐다고 밝혔다. "
    "공사 관계자는 \"긴급 복구에 나섰다\"고 말했다. 피해 주민은 1,200명으로 집계됐다. 사고 원인은 조사 중이다. "
    "OpenAI와는 무관한 사고다."
)
PROMPT = "{article}|{headline}|{summary}|{points}"


def summ(summary="마곡동에서 열수송관이 파열돼 9천 세대의 난방이 중단됐다.", basis="body", status="ok", rank=1, points=("복구 진행",)):
    return EventSummary(rank=rank, event_title="t", event_url="u", headline="마곡동 난방 중단", summary=summary,
                        key_points=list(points), sources=[{"source": "연합", "title": "t", "url": "a1"}],
                        basis=basis, status=status)


def event(text=ARTICLE, rank=1):
    return TopEvent(rank=rank, title="t", url="a1", articles=[Article(url="a1", title="제목", text=text, text_status="ok")],
                    best_rank=1, source_count=1, source_status="ok", rank_score=1, source_score=0, total_score=1,
                    comparison_basis="single")


def reply(*claims):
    return json.dumps({"claims": [{"text": t, "supported": s, "evidence": e} for t, s, e in claims]}, ensure_ascii=False)


GOOD = reply(("열수송관이 파열됐다", True, "마곡동 일대 열수송관이 파열돼 약 9천 세대의 난방과 온수 공급이 중단됐다고 밝혔다"))


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"message": {"content": r}}


# ---- 코드 검사 ----
def test_rule_check_passes_matching_facts():
    assert rule_check("9천 세대, 1200명 피해, \"긴급 복구에 나섰다\" 라고 했다.", ARTICLE) == []


def test_rule_check_number_variants_accepted():
    assert rule_check("약 9000 세대", ARTICLE) == []      # 9천 == 9000
    assert rule_check("1,200명", ARTICLE) == []           # 쉼표 무시
    assert rule_check("1200명", ARTICLE) == []


def test_rule_check_finds_invented_number_quote_and_name():
    issues = rule_check("피해 주민은 5,000명이다. 그는 \"전원 해임하겠다\"고 했다. Samsung이 참여했다.", ARTICLE)
    assert any("5,000" in i for i in issues)
    assert any("인용문" in i for i in issues)
    assert any("Samsung" in i for i in issues)


def test_rule_check_present_latin_ok():
    assert rule_check("OpenAI", ARTICLE) == []


# ---- 근거 문장 확인 ----
def test_evidence_found():
    assert evidence_found("마곡동 일대 열수송관이 파열돼 약 9천 세대의 난방과 온수 공급이 중단됐다", ARTICLE)
    assert evidence_found("마곡동 일대  열수송관이 파열되어 약 9천 세대 난방 온수 공급이 중단", ARTICLE)  # 약간 다른 표현
    assert not evidence_found("정부는 책임자 전원을 해임하기로 결정했다", ARTICLE)
    assert not evidence_found("", ARTICLE) and not evidence_found("짧음", ARTICLE)


# ---- LLM 검토 ----
def test_pass_when_everything_supported():
    r = verify_one(summ(), ARTICLE, FakeClient([GOOD]), "m", PROMPT)
    assert r.status == "pass" and r.llm_status == "ok" and r.rule_issues == [] and r.attempts == 1


def test_flag_when_llm_marks_unsupported():
    bad = reply(("열수송관이 파열됐다", True, "마곡동 일대 열수송관이 파열돼 약 9천 세대의 난방과 온수 공급이 중단됐다고 밝혔다"),
                ("책임자 전원을 해임했다", False, ""))
    r = verify_one(summ(), ARTICLE, FakeClient([bad]), "m", PROMPT)
    assert r.status == "flag" and [c["verdict"] for c in r.claims] == ["supported", "unsupported"]


def test_warn_when_model_invents_evidence():
    fake_evidence = reply(("정부가 조치했다", True, "정부는 이번 사고의 책임자를 전원 해임하기로 결정했다"))
    r = verify_one(summ(), ARTICLE, FakeClient([fake_evidence]), "m", PROMPT)
    assert r.claims[0]["verdict"] == "unverified" and r.status == "warn"


def test_flag_from_rule_even_if_llm_says_fine():
    r = verify_one(summ(summary="피해 주민은 5,000명이다."), ARTICLE, FakeClient([GOOD]), "m", PROMPT)
    assert r.status == "flag" and any("5,000" in i for i in r.rule_issues)


def test_retry_then_ok_and_failure_is_warn():
    r = verify_one(summ(), ARTICLE, FakeClient(["x", GOOD]), "m", PROMPT)
    assert r.status == "pass" and r.attempts == 2
    r = verify_one(summ(), ARTICLE, FakeClient(["x", "y", "z"]), "m", PROMPT)
    assert r.llm_status == "failed" and r.status == "warn" and r.notes
    r = verify_one(summ(), ARTICLE, FakeClient([ConnectionError("refused")] * 3), "m", PROMPT)
    assert r.llm_status == "failed" and "ConnectionError" in r.notes[0]


def test_title_only_skips_llm_but_runs_rules():
    c = FakeClient([])
    r = verify_one(summ(basis="title_only", summary="피해자가 7,777명이다."), "마곡동 난방 중단", c, "m", PROMPT)
    assert r.llm_status == "skipped" and c.calls == [] and r.status == "flag"


def test_skipped_cases():
    assert verify_one(summ(status="failed"), ARTICLE, FakeClient([]), "m", PROMPT).status == "skipped"
    assert verify_one(summ(), "", FakeClient([]), "m", PROMPT).status == "skipped"


def test_prompt_filled_and_model_passed():
    c = FakeClient([GOOD])
    verify_one(summ(), ARTICLE, c, "judge:3b", PROMPT)
    assert c.calls[0]["model"] == "judge:3b" and "{article}" not in c.calls[0]["messages"][0]["content"]
    assert ARTICLE[:20] in c.calls[0]["messages"][0]["content"]


def test_verify_all_uses_article_actually_summarized():
    # 요약에 쓴 기사는 a2(대체 기사). 대표 기사 a1의 본문이 아니라 a2 본문으로 검토해야 한다.
    ev = event()
    ev.articles.append(Article(url="a2", title="다른", text="다른 본문입니다. 피해 주민은 5,000명이다.", text_status="ok"))
    s = summ(summary="피해 주민은 5,000명이다.", basis="body_fallback")
    s.sources = [{"source": "KBS", "title": "다른", "url": "a2"}]
    r = verify_all([s], [ev], FakeClient([GOOD]), "m", PROMPT)[0]
    assert r.rule_issues == []


# ---- 자체 점검 ----
def test_selftest_counts_detection_and_false_alarms():
    sane = GOOD
    caught = reply(("열수송관이 파열됐다", True, "마곡동 일대 열수송관이 파열돼 약 9천 세대의 난방과 온수 공급이 중단됐다고 밝혔다"),
                   (FAKE_CLAIM, False, ""))
    r = selftest([summ()], [event()], FakeClient([sane, caught]), "m", PROMPT)
    assert (r["사건수"], r["가짜_적발"], r["원본_오탐"]) == (1, 1, 0)
    missed = selftest([summ()], [event()], FakeClient([sane, sane]), "m", PROMPT)
    assert missed["가짜_적발"] == 0
    noisy = reply(("열수송관이 파열됐다", False, ""))
    r = selftest([summ()], [event()], FakeClient([noisy, noisy]), "m", PROMPT)
    assert r["원본_오탐"] == 1 and r["가짜_적발"] == 1


def test_code_only_mode_needs_no_model_and_makes_no_calls():
    c = FakeClient([])
    out = verify_all([summ(), summ(rank=2, summary="피해 주민은 5,000명이다.")], [event(rank=1), event(rank=2)],
                     c, "", "", use_llm=False)
    assert c.calls == [] and [r.status for r in out] == ["pass", "flag"]
    assert all(r.llm_status == "not_run" for r in out)


def test_run_default_is_code_only(tmp_path, monkeypatch):
    from newscard import verify
    d = tmp_path / "data"
    d.mkdir()
    (d / "summaries.json").write_text(json.dumps([summ().model_dump()], ensure_ascii=False), encoding="utf-8")
    (d / "top_events.json").write_text(json.dumps([event().model_dump()], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(verify, "make_client", lambda: (_ for _ in ()).throw(AssertionError("모델을 쓰면 안 됨")))
    res = verify.run(str(d))
    assert res[0].status == "pass" and (d / "verification.json").exists()


def test_code_check_speed_on_long_articles():
    import time
    big = ("서울에너지공사는 7일 새벽 마곡동에서 약 9천 세대의 난방이 중단됐다고 밝혔다. " * 160)  # 약 1.2만자
    s = summ(summary="마곡동에서 9천 세대의 난방이 중단됐다. 공사는 \"긴급 복구\"를 밝혔다. OpenAI는 무관하다.")
    t = time.perf_counter()
    for _ in range(5):
        rule_check(summary_text_of(s), big)
    assert time.perf_counter() - t < 1.0  # 5건 합쳐 1초 미만
