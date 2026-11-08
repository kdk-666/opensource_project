"""Ollama 없이 가짜 클라이언트로 대본 단계를 검증한다."""
import json
from datetime import datetime

import pytest

from newscard.models import EventSummary
from newscard.script import (MAX_NARRATION, build_script, check_narration, est_seconds,
                             fallback_narration, grounding, source_line)

PROMPT = "{max_narration}|{headline}|{summary}|{points}"
TODAY = datetime(2026, 11, 8)

GOOD_TEXT = ("서울 마곡동에서 열수송관이 터져 약 9천 세대의 난방과 온수가 끊겼습니다. "
             "서울에너지공사는 긴급 복구에 나섰고, 사고 원인은 조사 중이라고 밝혔습니다.")
LIST_TEXT = "마곡동 열수송관 파열, 9천 세대 난방 온수 중단, 서울에너지공사 긴급 복구, 원인 조사 중."
INVENT_TEXT = ("마곡동 열수송관이 터져 9천 세대가 큰 불편을 겪고 있습니다. "
               "시민들은 분노하고 있으며 정부의 대책이 시급합니다.")
NUMBER_TEXT = ("서울 마곡동에서 열수송관이 터져 약 5천 세대의 난방과 온수가 끊겼습니다. "
               "서울에너지공사는 긴급 복구에 나섰다고 밝혔습니다.")


def nar(text=GOOD_TEXT):
    return json.dumps({"narration": text}, ensure_ascii=False)


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"message": {"content": r}}


def summary(rank=1, status="ok", basis="body", also=()):
    return EventSummary(
        rank=rank, event_title=f"사건{rank}", event_url=f"u{rank}", headline="마곡동 난방 중단",
        summary=("서울 마곡동의 열수송관이 파열돼 약 9천 세대의 난방과 온수 공급이 중단됐다. "
                 "서울에너지공사는 긴급 복구에 나섰으며 원인은 조사 중이라고 밝혔다."),
        key_points=["열수송관 파열", "약 9천 세대 난방 중단", "원인 조사 중"],
        sources=[{"source": "연합뉴스", "title": "t", "url": "u"}], also_reported_by=list(also),
        basis=basis, status=status,
    )


# ---- 나레이션 검사 (코드) ----
def test_check_accepts_good_narration():
    assert check_narration(GOOD_TEXT, summary()) == ""


@pytest.mark.parametrize("text,reason", [
    (LIST_TEXT, "끝나지 않음"),               # 단어 나열
    (INVENT_TEXT, "일치율"),                   # 요약에 없는 평가/감정
    (NUMBER_TEXT, "5"),                        # 요약에 없는 숫자
    (GOOD_TEXT + " (자세한 내용은 링크 참고)", "기호"),  # 괄호
])
def test_check_rejects_bad_narration(text, reason):
    assert reason in check_narration(text, summary())


def test_grounding_values():
    src = "마곡동 난방 중단 " + summary().summary
    assert grounding(GOOD_TEXT, src) > 0.6 and grounding(INVENT_TEXT, src) < 0.4


# ---- 대본 생성 ----
def test_structure_intro_news_outro():
    cards = build_script([summary(2), summary(1)], FakeClient([nar(), nar()]), "m", PROMPT, today=TODAY)
    assert [x.kind for x in cards] == ["intro", "news", "news", "outro"]
    assert [x.index for x in cards] == [0, 1, 2, 3]
    assert [x.rank for x in cards if x.kind == "news"] == [1, 2]
    assert "11월 8일" in cards[0].narration and "2가지" in cards[0].narration
    assert "인공지능" in cards[-1].narration


def test_title_and_bullets_come_from_summary_and_narration_from_model():
    c = FakeClient([nar()])
    card = build_script([summary()], c, "m", PROMPT, today=TODAY)[1]
    assert card.generated_by == "llm" and card.narration == GOOD_TEXT and card.attempts == 1
    assert card.title == "마곡동 난방 중단" and card.bullets == ["열수송관 파열", "약 9천 세대 난방 중단", "원인 조사 중"]
    sent = c.calls[0]["messages"][0]["content"]
    assert sent.startswith(f"{MAX_NARRATION}|마곡동 난방 중단|") and "- 열수송관 파열" in sent
    assert c.calls[0]["format"] == "json"


@pytest.mark.parametrize("bad", [
    "JSON 아님",
    nar(LIST_TEXT),
    nar(INVENT_TEXT),
    nar(NUMBER_TEXT),
    nar("짧다."),                              # 너무 짧음
    nar("가나다라마바사. " * 30),               # 너무 김
])
def test_bad_output_is_rejected_and_retried(bad):
    c = FakeClient([bad, nar()])
    card = build_script([summary()], c, "m", PROMPT, today=TODAY)[1]
    assert card.generated_by == "llm" and card.attempts == 2 and card.narration == GOOD_TEXT


def test_fallback_after_three_rejections():
    c = FakeClient([nar(LIST_TEXT), nar(INVENT_TEXT), "x"])
    card = build_script([summary()], c, "m", PROMPT, today=TODAY)[1]
    assert card.generated_by == "fallback" and card.attempts == 3
    assert "끝나지 않음" in card.error and "일치율" in card.error
    assert card.narration.startswith("서울 마곡동의 열수송관이") and len(card.narration) <= MAX_NARRATION
    assert card.title == "마곡동 난방 중단"  # 제목/줄은 그대로


def test_connection_error_falls_back_without_crash():
    card = build_script([summary()], FakeClient([ConnectionError("refused")] * 3), "m", PROMPT, today=TODAY)[1]
    assert card.generated_by == "fallback" and "ConnectionError" in card.error


def test_failed_summaries_are_skipped():
    cards = build_script([summary(1, status="failed"), summary(2)], FakeClient([nar()]), "m", PROMPT, today=TODAY)
    assert [x.rank for x in cards if x.kind == "news"] == [2]
    assert "1가지" in cards[0].narration


def test_no_valid_summaries_gives_empty():
    assert build_script([summary(1, status="failed")], FakeClient([]), "m", PROMPT, today=TODAY) == []
    assert build_script([], FakeClient([]), "m", PROMPT, today=TODAY) == []


def test_source_line():
    assert source_line(summary()) == "출처: 연합뉴스"
    assert source_line(summary(also=["KBS", "MBC"])) == "출처: 연합뉴스 외 2곳 보도"
    s = summary()
    s.sources = [{"source": "", "title": "t", "url": "u"}]
    assert source_line(s) == "출처: 언론사 미상"
    s.sources = []
    assert source_line(s) == ""


def test_basis_is_carried_to_card():
    cards = build_script([summary(basis="title_only")], FakeClient([nar()]), "m", PROMPT, today=TODAY)
    assert cards[1].basis == "title_only"


def test_fallback_narration_limits():
    assert fallback_narration("가" * 200 + ".", "제목입니다") == "제목입니다"
    assert fallback_narration("짧은 문장입니다.", "제목") == "짧은 문장입니다."
    assert len(fallback_narration("가나다라마. " * 50, "제목")) <= MAX_NARRATION


def test_est_seconds():
    assert est_seconds("가" * 55) == 10.0
    assert est_seconds("가 나 다") == pytest.approx(0.5)


def test_real_prompt_file_has_example_and_placeholders():
    from pathlib import Path
    text = Path("prompts/script.txt").read_text(encoding="utf-8")
    for key in ("{max_narration}", "{headline}", "{summary}", "{points}", "[예시]"):
        assert key in text
    assert "한빛시" in text  # 가상 예시(실제 뉴스와 다른 소재)


# ---- 길이 초과 / 형식 오류 원인 확인 ----
LONG_GOOD = GOOD_TEXT + " 서울 마곡동의 열수송관이 파열돼 약 9천 세대의 난방과 온수 공급이 중단됐다고 밝혔습니다. 서울에너지공사는 원인을 조사 중이라고 밝혔습니다."


def test_too_long_narration_is_trimmed_by_sentence_not_rejected():
    from newscard.script import fit_to_limit
    assert len(LONG_GOOD) > MAX_NARRATION
    fitted = fit_to_limit(LONG_GOOD)
    # 뒤 문장부터 빼고, 문장 중간은 자르지 않는다
    assert len(fitted) <= MAX_NARRATION and LONG_GOOD.startswith(fitted) and fitted.endswith("다.")
    assert fitted.startswith(GOOD_TEXT) and fitted != LONG_GOOD
    card = build_script([summary()], FakeClient([nar(LONG_GOOD)]), "m", PROMPT, today=TODAY)[1]
    assert card.generated_by == "llm" and card.attempts == 1 and card.narration == fitted


def test_single_overlong_sentence_still_rejected_with_reason():
    one = "서울 마곡동에서 열수송관이 터져 " + "약 9천 세대의 난방과 온수가 끊겼고 " * 8 + "밝혔습니다."
    card = build_script([summary()], FakeClient([nar(one)] * 3), "m", PROMPT, today=TODAY)[1]
    assert card.generated_by == "fallback" and "자를 넘음" in card.error


def test_wrong_json_key_reports_which_field_failed():
    wrong = json.dumps({"text": GOOD_TEXT}, ensure_ascii=False)
    c = FakeClient([wrong, nar()])
    card = build_script([summary()], c, "m", PROMPT, today=TODAY)[1]
    assert card.generated_by == "llm" and card.attempts == 2
    c2 = FakeClient([wrong] * 3)
    card2 = build_script([summary()], c2, "m", PROMPT, today=TODAY)[1]
    assert "narration" in card2.error and "ValidationError" in card2.error  # 어느 항목이 틀렸는지 보임


def test_rejection_reason_is_sent_back_to_the_model():
    c = FakeClient([nar(LIST_TEXT), nar()])
    build_script([summary()], c, "m", PROMPT, today=TODAY)
    first, second = (x["messages"][0]["content"] for x in c.calls)
    assert "거절된 이유" not in first
    assert "거절된 이유" in second and "끝나지 않음" in second


# ---- 숫자 범위 표현 / 어색한 수량 표현 ----
def range_summary():
    s = summary()
    s.headline = "초등 입학생 감소"
    s.summary = "내년 초등학교 입학생이 30만 명 미만으로 줄어들 전망이다. 저출생의 영향이라고 교육부는 밝혔다."
    s.key_points = ["입학생 30만 명 미만", "저출생 영향"]
    return s


def test_dropping_range_word_is_rejected():
    s = range_summary()
    bad = "내년 초등학교 입학 예정인 30만 명이 아래로 줄어들 것으로 예상됩니다. 교육부는 저출생의 영향이라고 밝혔습니다."
    assert "미만" in check_narration(bad, s)
    good = "내년 초등학교 입학생이 30만 명 미만으로 줄어들 전망입니다. 교육부는 저출생의 영향이라고 밝혔습니다."
    assert check_narration(good, s) == ""


def test_odd_quantity_particle_is_rejected_even_without_range_word():
    s = range_summary()
    odd = "내년 초등학교 입학생 30만 명이 아래로 줄어듭니다. 교육부는 저출생의 영향이라고 밝혔습니다. 미만이라는 설명입니다."
    assert "어색" in check_narration(odd, s)


def test_range_word_not_required_when_not_attached_to_number():
    s = summary()
    s.summary += " 이상 징후는 없다고 밝혔다."  # 숫자에 붙지 않은 '이상'은 범위 표현이 아니다
    assert check_narration(GOOD_TEXT, s) == ""


def test_rejection_feedback_names_the_missing_range_word():
    s = range_summary()
    bad = nar("내년 초등학교 입학생 30만 명이 줄어들 것으로 예상됩니다. 교육부는 저출생의 영향이라고 밝혔습니다.")
    good = nar("내년 초등학교 입학생이 30만 명 미만으로 줄어들 전망입니다. 교육부는 저출생의 영향이라고 밝혔습니다.")
    c = FakeClient([bad, good])
    card = build_script([s], c, "m", PROMPT, today=TODAY)[1]
    assert card.generated_by == "llm" and card.attempts == 2
    assert "'미만'" in c.calls[1]["messages"][0]["content"]
