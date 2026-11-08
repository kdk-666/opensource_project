"""Ollama 없이 가짜 클라이언트로 요약 단계를 검증한다."""
import json

from newscard.models import Article, TopEvent
from newscard.summarize import MAX_BODY_CHARS, build_material, choose_article, pick_num_ctx, summarize_all, summarize_event

PROMPT = "규칙...\n[자료]\n{material}"
GOOD = json.dumps({"headline": "제목", "summary": "요약 문장입니다.", "key_points": ["가", "나", "다", "라"]}, ensure_ascii=False)


class FakeClient:
    """미리 정해 둔 응답을 차례로 돌려준다. 예외 객체면 던진다."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"message": {"content": r}}


def event(rank=1, with_text=True) -> TopEvent:
    arts = [
        Article(url="u1", title="대표 제목", source="연합뉴스", rank=1,
                text="본문 내용입니다. " * 30 if with_text else "", text_status="ok" if with_text else "failed"),
        Article(url="u2", title="관련 제목", source="KBS", kind="related", parents=[1],
                text="관련 본문입니다. " * 30 if with_text else "", text_status="ok" if with_text else "failed"),
    ]
    return TopEvent(rank=rank, title="대표 제목", url="u1", source="연합뉴스", articles=arts, best_rank=1,
                    source_count=2, source_status="ok", rank_score=1, source_score=0.25, total_score=0.9,
                    comparison_basis="body")


def test_success():
    s = summarize_event(event(), FakeClient([GOOD]), "m", PROMPT)
    assert s.status == "ok" and s.headline == "제목" and s.basis == "body"
    assert len(s.key_points) == 3  # 최대 3개로 제한
    assert [x["source"] for x in s.sources] == ["연합뉴스"]  # 요약에 쓴 기사는 대표 기사뿐
    assert s.also_reported_by == ["KBS"]


def test_retry_on_bad_json_then_success():
    c = FakeClient(["이건 JSON이 아님", '{"headline": ""}', GOOD])
    s = summarize_event(event(), c, "m", PROMPT, retries=2)
    assert s.status == "ok" and len(c.calls) == 3


def test_fails_after_retries_without_crashing():
    c = FakeClient(["x", "y", "z"])
    s = summarize_event(event(), c, "m", PROMPT, retries=2)
    assert s.status == "failed" and s.error and s.summary == ""


def test_connection_error_recorded():
    s = summarize_event(event(), FakeClient([ConnectionError("refused")] * 3), "m", PROMPT)
    assert s.status == "failed" and "ConnectionError" in s.error


def test_only_representative_body_is_sent_in_full():
    ev = event()
    ev.articles[0].text = "대표본문시작 " + "가나다라 " * 1000 + "대표본문끝"  # 1000자 넘는 본문
    material, basis, truncated = build_material(ev)
    assert basis == "body" and not truncated
    assert "대표본문시작" in material and "대표본문끝" in material  # 전체가 들어감
    assert "관련 본문" not in material and "KBS" not in material  # 관련 기사는 쓰지 않음


def test_very_long_body_truncated_and_flagged():
    ev = event()
    ev.articles[0].text = "가" * (MAX_BODY_CHARS + 500)
    material, _, truncated = build_material(ev)
    assert truncated and len(material) < MAX_BODY_CHARS + 200
    s = summarize_event(ev, FakeClient([GOOD]), "m", PROMPT)
    assert s.truncated and s.input_chars == len(material)


def test_num_ctx_scales_with_input():
    assert pick_num_ctx(500) == 4096
    assert pick_num_ctx(5000) == 7168  # (5000+1500)을 1024 단위로 올림
    assert pick_num_ctx(10**6) == 16384
    c = FakeClient([GOOD])
    summarize_event(event(), c, "m", PROMPT)
    assert c.calls[0]["options"]["num_ctx"] >= 4096


def test_fallback_to_related_body_when_representative_has_none():
    ev = event()
    ev.articles[0].text, ev.articles[0].text_status = "", "failed"  # 대표만 실패, 관련 기사 본문은 있음
    material, basis, _ = build_material(ev)
    assert basis == "body_fallback" and "관련 본문" in material
    s = summarize_event(ev, FakeClient([GOOD]), "m", PROMPT)
    assert s.basis == "body_fallback"
    assert s.event_title == "대표 제목"  # 사건 정보는 그대로 대표 기사
    assert s.sources == [{"source": "KBS", "title": "관련 제목", "url": "u2"}]  # 실제로 쓴 기사만 출처
    assert s.also_reported_by == ["연합뉴스"]


def test_fallback_prefers_other_representative_over_related():
    ev = event()
    ev.articles[0].text, ev.articles[0].text_status = "", "failed"
    other = Article(url="u3", title="다른 항목 대표", source="MBC", rank=5, text="다른 대표 본문. " * 30, text_status="ok")
    ev.articles.insert(1, other)
    _, basis, _ = build_material(ev)
    art, _ = choose_article(ev)
    assert basis == "body_fallback" and art.url == "u3"


def test_fallback_skips_failed_and_short_articles():
    ev = event()
    for a in ev.articles:
        a.text, a.text_status = "", "failed"
    ev.articles[1].text, ev.articles[1].text_status = "짧음", "too_short"
    assert build_material(ev)[1] == "title_only"


def test_title_only_when_no_body():
    material, basis, _ = build_material(event(with_text=False))
    assert basis == "title_only" and "본문 내용" not in material and "대표 제목" in material
    s = summarize_event(event(with_text=False), FakeClient([GOOD]), "m", PROMPT)
    assert s.basis == "title_only"


def test_body_truncated_and_prompt_filled():
    c = FakeClient([GOOD])
    summarize_event(event(), c, "m", PROMPT)
    sent = c.calls[0]["messages"][0]["content"]
    assert "{material}" not in sent and "연합뉴스 - 대표 제목" in sent
    assert c.calls[0]["format"] == "json"


def test_one_failure_does_not_stop_others(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "summarize.txt").write_text(PROMPT, encoding="utf-8")
    c = FakeClient(["bad", "bad", "bad", GOOD])
    out = summarize_all([event(1), event(2)], client=c, model="m")
    assert [s.status for s in out] == ["failed", "ok"]


class MetricsClient(FakeClient):
    """Ollama처럼 통계값(토큰 수, 시간)이 붙은 응답을 돌려준다."""

    def chat(self, **kw):
        r = super().chat(**kw)
        r.update(prompt_eval_count=1234, eval_count=210, load_duration=2_500_000_000)
        return r


def test_timing_and_token_metrics_recorded():
    s = summarize_event(event(), MetricsClient([GOOD]), "m", PROMPT)
    assert s.attempts == 1 and s.elapsed_sec >= 0
    assert s.prompt_tokens == 1234 and s.output_tokens == 210 and s.load_sec == 2.5


def test_timing_without_metrics_does_not_crash():
    s = summarize_event(event(), FakeClient([GOOD]), "m", PROMPT)
    assert s.prompt_tokens is None and s.load_sec is None and s.elapsed_sec >= 0


def test_timing_counts_retries_and_failures():
    s = summarize_event(event(), FakeClient(["x", "y", "z"]), "m", PROMPT, retries=2)
    assert s.status == "failed" and s.attempts == 3 and s.elapsed_sec >= 0
    s = summarize_event(event(), FakeClient(["x", GOOD]), "m", PROMPT)
    assert s.status == "ok" and s.attempts == 2


def test_cli_timing_line():
    from newscard.cli import _timing_line

    ok = summarize_event(event(), MetricsClient([GOOD]), "m", PROMPT)
    line = _timing_line([ok], 12.34)
    assert "전체 12.3초" in line and "성공 1/1건" in line and "모델 로딩 최대 2.5초" in line
    assert "성공한 요약 없음" in _timing_line([], 1.0)
