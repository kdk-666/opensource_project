"""음성 단계 테스트. Piper 없이 가짜 합성 함수로 검증한다."""
import json
import wave

import pytest

from newscard import tts
from newscard.models import ScriptCard
from newscard.tts import native, normalize_for_tts as norm, sino


def fake_synth(seconds_per_char=0.1, rate=22050):
    """글자 수에 비례하는 길이의 무음 WAV를 만든다."""
    def synth(text, path):
        n = int(len(text.replace(" ", "")) * seconds_per_char * rate)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"\x00\x00" * n)
    return synth


def card(index=1, kind="news", rank=1, narration="서울 마곡동에서 열수송관이 터져 약 9천 세대의 난방이 끊겼습니다."):
    return ScriptCard(index=index, kind=kind, rank=rank, title="t", narration=narration, est_sec=7.0)


# ---- 숫자 읽기 ----
@pytest.mark.parametrize("n,expected", [(0, "영"), (7, "칠"), (10, "십"), (11, "십일"), (100, "백"), (1234, "천이백삼십사"),
                                         (2026, "이천이십육"), (10000, "만"), (100000000, "일억"),
                                         (12345, "만 이천삼백사십오"),            # 만 단위마다 띄어 쓴다
                                         (12345678, "천이백삼십사만 오천육백칠십팔"), (100010000, "일억 일만")])
def test_sino(n, expected):
    assert sino(n) == expected


@pytest.mark.parametrize("n,expected", [(1, "한"), (2, "두"), (3, "세"), (5, "다섯"), (10, "열"), (11, "열한"), (20, "스무"),
                                         (21, "스물한"), (99, "아흔아홉")])
def test_native(n, expected):
    assert native(n) == expected


@pytest.mark.parametrize("text,expected", [
    ("11월 8일", "십일 월 팔 일"),                            # 단위 명사는 띄어 쓴다
    ("6월 10월", "유월 시월"),                                # 예외 읽기는 한 단어
    ("2026년", "이천이십육 년"),
    ("약 9천 세대", "약 구천 세대"),                           # 천/만/억 앞 숫자는 한자어, 수의 일부라 붙여 쓴다
    ("5가지", "다섯 가지"), ("20명", "스무 명"), ("3곳", "세 곳"), ("4시간", "네 시간"),  # 고유어 + 띄어쓰기
    ("100명", "백 명"),                                        # 100 이상은 한자어
    ("1,200명", "천이백 명"),                                  # 쉼표 제거
    ("7.6조 원", "칠 점 육조 원"),                              # 소수
    ("25%", "이십오 퍼센트"),
    ("3분", "삼 분"),
    ("10 명", "열 명"),                                        # 띄어 써도 단위 인식
    ("12345678원", "천이백삼십사만 오천육백칠십팔 원"),
    ("1위를 차지했다", "일 위를 차지했다"),
    ("5G 서비스", "파이브지 서비스"),
])
def test_normalize_numbers(text, expected):
    assert norm(text) == expected


def test_normalize_strips_quotes_and_spaces():
    assert norm("“긴급 복구”라고   밝혔다") == "긴급 복구라고 밝혔다"
    assert norm("") == "" and norm("   ") == ""
    assert norm("숫자 없는 문장입니다.") == "숫자 없는 문장입니다."


def test_intro_sentence():
    assert norm("11월 8일, 오늘의 주요 뉴스 5가지를 전해드립니다.") == "십일 월 팔 일, 오늘의 주요 뉴스 다섯 가지를 전해드립니다."


# ---- 카드별 음성 ----
def test_synthesize_cards_writes_wavs_and_real_duration(tmp_path):
    cs = [card(0, "intro", 0, "오늘의 주요 뉴스를 전해드립니다."), card(1), card(2, "outro", 0, "이상입니다. 감사합니다.")]
    items = tts.synthesize_cards(cs, fake_synth(), tmp_path / "audio")
    assert [i["status"] for i in items] == ["ok"] * 3
    for i in items:
        with wave.open(i["audio"], "rb") as w:
            assert w.getframerate() == 22050
        assert i["duration_sec"] == tts.wav_seconds(tmp_path / "audio" / i["audio"].split("/")[-1])
    assert items[1]["audio"].endswith("card_01_news_1.wav")
    assert items[1]["spoken_text"] == norm(cs[1].narration) and "구천" in items[1]["spoken_text"]
    assert items[1]["duration_sec"] > 0


def test_old_audio_is_removed(tmp_path):
    out = tmp_path / "audio"
    out.mkdir()
    (out / "card_99_old.wav").write_bytes(b"old")
    tts.synthesize_cards([card()], fake_synth(), out)
    assert not (out / "card_99_old.wav").exists()


def test_one_failure_does_not_stop_others(tmp_path):
    def flaky(text, path):
        if "터져" in text:
            raise RuntimeError("엔진 오류")
        fake_synth()(text, path)
    items = tts.synthesize_cards([card(1), card(2, narration="두 번째 카드입니다. 정상 문장입니다.")], flaky, tmp_path / "a")
    assert [i["status"] for i in items] == ["failed", "ok"]
    assert "RuntimeError" in items[0]["error"] and not (tmp_path / "a" / "card_01_news_1.wav").exists()


def test_empty_narration_and_too_short_audio_fail_cleanly(tmp_path):
    items = tts.synthesize_cards([card(1, narration="   ")], fake_synth(), tmp_path / "a")
    assert items[0]["status"] == "failed" and "비어" in items[0]["error"]
    items = tts.synthesize_cards([card(1, narration="네")], fake_synth(seconds_per_char=0.01), tmp_path / "b")
    assert items[0]["status"] == "failed" and "너무 짧음" in items[0]["error"]


def test_long_audio_gets_warning(tmp_path):
    items = tts.synthesize_cards([card(1, narration="가" * 100)], fake_synth(seconds_per_char=0.5), tmp_path / "a")
    assert items[0]["status"] == "ok" and any("깁니다" in w for w in items[0]["warnings"])


# ---- 음성 모델 / run ----
def test_ensure_voice_skips_download_when_present(tmp_path, monkeypatch):
    (tmp_path / "v.onnx").write_bytes(b"x")
    (tmp_path / "v.onnx.json").write_text("{}")
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("내려받으면 안 됨")))
    assert tts.ensure_voice("v", str(tmp_path)) == tmp_path / "v.onnx"


def test_ensure_voice_downloads_with_documented_command(tmp_path, monkeypatch):
    seen = {}

    class P:
        returncode = 0
        stdout = stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        (tmp_path / "v.onnx").write_bytes(b"x")
        (tmp_path / "v.onnx.json").write_text("{}")
        return P()

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    tts.ensure_voice("v", str(tmp_path))
    assert seen["cmd"][1:] == ["-m", "piper.download_voices", "v", "--data-dir", str(tmp_path)]


def test_ensure_voice_failure_message(tmp_path, monkeypatch):
    class P:
        returncode = 1
        stdout = ""
        stderr = "URLError: 연결 실패"

    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: P())
    with pytest.raises(tts.VoiceMissing, match="인터넷"):
        tts.ensure_voice("v", str(tmp_path))


def test_run_requires_script(tmp_path):
    with pytest.raises(SystemExit, match="newscard script"):
        tts.run(str(tmp_path))


def test_run_writes_audio_json(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "script.json").write_text(json.dumps([card(0, "intro", 0).model_dump(), card(1).model_dump()], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tts, "ensure_voice", lambda *a, **k: tmp_path / "v.onnx")
    monkeypatch.setattr(tts, "make_synth", lambda model, scale=None: fake_synth())
    items = tts.run(str(d))
    saved = json.loads((d / "audio.json").read_text(encoding="utf-8"))
    assert len(items) == len(saved) == 2 and (d / "audio" / "card_01_news_1.wav").exists()


def test_run_voice_missing_exits_with_message(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    (d / "script.json").write_text(json.dumps([card().model_dump()], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tts, "ensure_voice", lambda *a, **k: (_ for _ in ()).throw(tts.VoiceMissing("받지 못했어요")))
    with pytest.raises(SystemExit, match="받지 못했어요"):
        tts.run(str(d))


# ---- 영어 읽기 ----
@pytest.mark.parametrize("text,expected", [
    ("SNS에서 화제", "에스엔에스에서 화제"),                 # 조사가 붙어 있어도
    ("AI 기술", "에이아이 기술"),
    ("KBS 뉴스", "케이비에스 뉴스"),
    ("5G와 4G", "파이브지와 포지"),
    ("sns도", "에스엔에스도"),                                # 대소문자 구분 없음(사전)
    ("OpenAI가 발표", "오픈에이아이가 발표"),
    ("ABC 회사", "에이비씨 회사"),                            # 사전에 없는 대문자 약어는 글자 이름대로
    ("GPT-5", "지피티-오"),
])
def test_english_is_read_in_hangul(text, expected):
    assert norm(text) == expected


def test_unknown_english_word_is_reported_not_guessed():
    unknown = []
    out = norm("Tesla가 공개했다. Tesla와 Nvidia", unknown=unknown)
    assert out.startswith("Tesla가") and unknown == ["Tesla", "Nvidia"]  # 그대로 두고 알려 준다(중복 없이)


def test_user_dictionary_overrides_default(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text("Tesla: 테슬라\nSNS: 소셜미디어\n", encoding="utf-8")
    table = tts.load_pronunciations(str(f))
    assert norm("Tesla와 SNS", table) == "테슬라와 소셜미디어" and table["AI"] == "에이아이"
    assert tts.load_pronunciations(str(tmp_path / "없음.yaml"))["AI"] == "에이아이"  # 파일이 없어도 기본 사전


def test_synthesize_cards_warns_about_unknown_english(tmp_path):
    items = tts.synthesize_cards([card(1, narration="Tesla가 SNS에서 화제가 됐습니다.")], fake_synth(), tmp_path / "a", {})
    assert items[0]["spoken_text"] == "Tesla가 에스엔에스에서 화제가 됐습니다."  # 빈 사전이어도 대문자 약어는 글자 이름대로
    assert any("Tesla" in w and "pronunciations.yaml" in w for w in items[0]["warnings"])
