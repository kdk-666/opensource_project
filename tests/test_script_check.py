"""대본 코드 검사 테스트. 모델도 네트워크도 쓰지 않는다."""
import json
import time
from datetime import datetime

from newscard.models import Article, ScriptCard, TopEvent
from newscard.script import MAX_NARRATION, build_script, make_intro, make_outro
from newscard.script_check import check_script
from tests.test_script import GOOD_TEXT, INVENT_TEXT, LIST_TEXT, FakeClient, nar, summary

TODAY = datetime(2026, 11, 8)
ARTICLE = ("서울에너지공사는 7일 새벽 마곡동 일대 열수송관이 파열돼 약 9천 세대의 난방과 온수 공급이 중단됐다고 밝혔다. "
           "긴급 복구에 나섰고 사고 원인은 조사 중이다.")


def event(rank=1, text=ARTICLE):
    return TopEvent(rank=rank, title="t", url="u", articles=[Article(url="u", title="제목", source="연합뉴스", text=text, text_status="ok")],
                    best_rank=1, source_count=1, source_status="ok", rank_score=1, source_score=0, total_score=1,
                    comparison_basis="single")


def good_script(n=1):
    sums = [summary(rank=i) for i in range(1, n + 1)]
    cards = build_script(sums, FakeClient([nar()] * n), "m", "{headline}{summary}{points}{max_narration}", today=TODAY)
    return cards, sums, [event(i) for i in range(1, n + 1)]


def card(cards, i):
    return cards[i].model_copy(deep=True)


def test_clean_script_passes():
    cards, sums, evs = good_script(2)
    rep = check_script(cards, sums, evs)
    assert rep["structure_issues"] == []
    assert [c["status"] for c in rep["cards"]] == ["pass"] * 4
    assert rep["total_est_sec"] > 0


def test_list_style_narration_flagged():
    cards, sums, evs = good_script()
    cards[1].narration = LIST_TEXT
    c = check_script(cards, sums, evs)["cards"][1]
    assert c["status"] == "flag" and any("끝나지 않음" in i for i in c["issues"])


def test_invented_content_flagged():
    cards, sums, evs = good_script()
    cards[1].narration = INVENT_TEXT
    c = check_script(cards, sums, evs)["cards"][1]
    assert c["status"] == "flag" and any("일치율" in i for i in c["issues"])


def test_number_not_in_summary_or_article_flagged():
    cards, sums, evs = good_script()
    cards[1].narration = GOOD_TEXT.replace("9천", "5천")
    issues = check_script(cards, sums, evs)["cards"][1]["issues"]
    assert any("요약에 없는" in i for i in issues)


def test_number_in_summary_but_not_in_article_flagged():
    # 요약은 맞게 썼어도 원문 기사에 없는 숫자면 걸린다 (요약 단계의 오류가 대본까지 온 경우)
    cards, sums, evs = good_script()
    evs[0] = event(1, text="마곡동에서 열수송관이 파열돼 난방이 중단됐다고 서울에너지공사는 밝혔다. 긴급 복구에 나섰다.")
    issues = check_script(cards, sums, evs)["cards"][1]["issues"]
    assert any("원문 기사 대조" in i and "9천" in i for i in issues)


def test_length_limits():
    cards, sums, evs = good_script()
    cards[1].narration = "가나다라마바사. " * 30
    assert any("최대" in i for i in check_script(cards, sums, evs)["cards"][1]["issues"])
    cards[1].narration = "짧다."
    assert any("너무 짧음" in i for i in check_script(cards, sums, evs)["cards"][1]["issues"])
    cards[1].narration = ""
    assert any("비어" in i for i in check_script(cards, sums, evs)["cards"][1]["issues"])


def test_symbols_flagged():
    cards, sums, evs = good_script()
    cards[1].narration = GOOD_TEXT + " (출처 참고)"
    assert any("기호" in i for i in check_script(cards, sums, evs)["cards"][1]["issues"])


def test_fallback_and_title_only_are_warnings():
    cards, sums, evs = good_script()
    cards[1].generated_by = "fallback"
    c = check_script(cards, sums, evs)["cards"][1]
    assert c["status"] == "warn" and any("대체" in w for w in c["warnings"])
    sums[0].basis = "title_only"
    c = check_script(cards, sums, evs)["cards"][1]
    assert any("제목만" in w for w in c["warnings"])


def test_title_bullets_source_modified_are_warnings():
    cards, sums, evs = good_script()
    cards[1].title = "다른 제목"
    cards[1].bullets = ["요약에 없는 줄"]
    cards[1].source_line = "출처: 다른곳"
    w = check_script(cards, sums, evs)["cards"][1]["warnings"]
    assert len(w) == 3
    cards[1].source_line = ""
    assert any("비어" in x for x in check_script(cards, sums, evs)["cards"][1]["warnings"])


def test_structure_problems():
    cards, sums, evs = good_script(2)
    assert "첫 카드가 인트로가 아님" in check_script(cards[1:], sums, evs)["structure_issues"]
    assert "마지막 카드가 아웃트로가 아님" in check_script(cards[:-1], sums, evs)["structure_issues"]
    swapped = [cards[0], cards[2], cards[1], cards[3]]
    assert any("순서" in i for i in check_script(swapped, sums, evs)["structure_issues"])
    assert any("없음" in i and "2" in i for i in check_script(cards[:2] + cards[3:], sums, evs)["structure_issues"])
    assert check_script([], sums, evs)["structure_issues"] == ["대본이 비어 있음"]


def test_intro_count_must_match():
    cards, sums, evs = good_script(2)
    cards[0].narration = "11월 8일, 오늘의 주요 뉴스 5가지를 전해드립니다."
    c = check_script(cards, sums, evs)["cards"][0]
    assert c["status"] == "flag" and "사건 수" in c["issues"][0]


def test_outro_without_ai_notice_warns():
    cards, sums, evs = good_script()
    cards[-1].narration = "이상 오늘의 주요 뉴스였습니다. 시청해 주셔서 감사합니다."
    c = check_script(cards, sums, evs)["cards"][-1]
    assert c["status"] == "warn"


def test_total_duration_warning():
    cards, sums, evs = good_script(1)
    for c in cards:
        c.narration = c.narration + " " + "가" * 400  # 길이 위반이지만 총 길이도 넘김
    rep = check_script(cards, sums, evs)
    assert any("영상 길이" in i for i in rep["structure_issues"])


def test_summary_missing_for_card():
    cards, sums, evs = good_script()
    c = check_script(cards, [], [])["cards"][1]
    assert c["status"] == "warn" and any("요약을 찾지 못" in w for w in c["warnings"])


def test_run_writes_report(tmp_path):
    from newscard import script_check
    cards, sums, evs = good_script()
    d = tmp_path / "data"
    d.mkdir()
    for name, items in (("script.json", cards), ("summaries.json", sums), ("top_events.json", evs)):
        (d / name).write_text(json.dumps([x.model_dump() for x in items], ensure_ascii=False), encoding="utf-8")
    rep = script_check.run(str(d))
    assert (d / "script_check.json").exists() and rep["cards"][1]["status"] == "pass"


def test_speed():
    cards, sums, evs = good_script(5)
    t = time.perf_counter()
    for _ in range(20):
        check_script(cards, sums, evs)
    assert (time.perf_counter() - t) / 20 < 0.1  # 한 번 검사에 0.1초 미만
