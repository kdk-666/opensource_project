"""음성 단계 테스트. Supertonic 없이 가짜 합성 함수/가짜 TTS 객체로 검증한다."""
import json
import types
import wave
from pathlib import Path

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
def test_run_requires_script(tmp_path):
    with pytest.raises(SystemExit, match="newscard script"):
        tts.run(str(tmp_path))


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


# ---- 엔진/목소리 설정 ----
def clear_env(monkeypatch):
    for k in ("NEWSCARD_VOICE", "NEWSCARD_TTS_SPEED", "NEWSCARD_TTS_STEPS", "NEWSCARD_TTS_NORMALIZE"):
        monkeypatch.delenv(k, raising=False)


# ---- Supertonic (가짜 TTS 객체) ----
class FakeProcessor:
    def __init__(self, bad=""):
        self.bad = set(bad)

    def validate_text(self, text):
        found = sorted(set(text) & self.bad)
        return (not found, found)


class FakeSupertonic:
    """supertonic.TTS와 같은 모양의 가짜. 호출 기록을 남기고 글자 수에 비례한 길이의 WAV를 쓴다."""
    instances = []

    def __init__(self, model=None, model_dir=None, auto_download=None, bad="", fail=None):
        if fail:
            raise fail
        self.args = dict(model=model, model_dir=model_dir, auto_download=auto_download)
        self.model = types.SimpleNamespace(text_processor=FakeProcessor(bad))
        self.calls, self.styles, self._last = [], [], 1.0
        FakeSupertonic.instances.append(self)

    def get_voice_style(self, name):
        if name not in tts.SUPERTONIC_VOICES:
            raise KeyError(name)
        self.styles.append(name)
        return ("style", name)

    def synthesize(self, text, voice_style, lang=None, **kw):
        self.calls.append(dict(text=text, style=voice_style, lang=lang, **kw))
        self._last = max(0.5, len(text.replace(" ", "")) * 0.1)
        return "wav", [self._last]

    def save_audio(self, wav, path):
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
            w.writeframes(b"\x00\x00" * int(self._last * 44100))


def factory(**extra):
    return lambda model=None, model_dir=None, auto_download=None: FakeSupertonic(model, model_dir, auto_download, **extra)


def test_supertonic_loads_model_into_given_dir_and_uses_korean(tmp_path):
    FakeSupertonic.instances.clear()
    synth = tts.make_supertonic_synth(tmp_path / "m", "M2", speed=1.1, steps=12, tts_factory=factory())
    out = tmp_path / "a.wav"
    synth("안녕하세요 뉴스입니다.", out)
    t = FakeSupertonic.instances[0]
    assert t.args == dict(model="supertonic-3", model_dir=str(tmp_path / "m"), auto_download=True)
    call = t.calls[0]
    assert call["lang"] == "ko" and call["style"] == ("style", "M2") and call["speed"] == 1.1 and call["total_steps"] == 12
    assert out.exists() and tts.wav_seconds(out) > 0.4


def test_supertonic_options_are_omitted_when_not_set(tmp_path):
    FakeSupertonic.instances.clear()
    tts.make_supertonic_synth(tmp_path, "F1", tts_factory=factory())("문장입니다.", tmp_path / "a.wav")
    assert set(FakeSupertonic.instances[0].calls[0]) == {"text", "style", "lang"}  # 속도/단계는 라이브러리 기본값 사용


def test_unsupported_characters_are_dropped_not_fatal(tmp_path):
    FakeSupertonic.instances.clear()
    synth = tts.make_supertonic_synth(tmp_path, "F1", tts_factory=factory(bad="★"))
    synth("속보 ★ 발생했습니다", tmp_path / "a.wav")
    assert FakeSupertonic.instances[0].calls[0]["text"] == "속보 발생했습니다"
    with pytest.raises(ValueError, match="읽을 수 있는 글자가 없음"):
        synth("★★", tmp_path / "b.wav")
    assert tts.drop_unsupported("ok", FakeProcessor()) == ("ok", [])


def test_supertonic_load_and_voice_errors_are_clear(tmp_path):
    with pytest.raises(tts.VoiceMissing, match="인터넷"):
        tts.make_supertonic_synth(tmp_path, "F1", tts_factory=factory(fail=OSError("연결 실패")))
    t = FakeSupertonic()
    with pytest.raises(tts.VoiceMissing, match="목소리"):
        tts.supertonic_synth(t, "Z9")


def test_run_passes_speed_steps_and_model_dir(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("NEWSCARD_VOICE", "M4")
    monkeypatch.setenv("NEWSCARD_TTS_SPEED", "1.0")
    seen = {}

    def fake_make(model_dir, voice, speed=None, steps=None, tts_factory=None):
        seen.update(model_dir=model_dir, voice=voice, speed=speed, steps=steps)
        return fake_synth()

    monkeypatch.setattr(tts, "make_supertonic_synth", fake_make)
    d = tmp_path / "data"
    d.mkdir()
    (d / "script.json").write_text(json.dumps([card(1).model_dump()], ensure_ascii=False), encoding="utf-8")
    items = tts.run(str(d))
    assert seen == dict(model_dir=d / "voices" / "supertonic3", voice="M4", speed=1.0, steps=None)
    assert items[0]["engine"] == "supertonic-3" and items[0]["voice"] == "M4"


def test_run_bad_settings_exit_with_message(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("NEWSCARD_VOICE", "ko_KR-kss-medium")
    d = tmp_path / "data"
    d.mkdir()
    (d / "script.json").write_text(json.dumps([card(1).model_dump()], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit, match="newscard tts samples"):
        tts.run(str(d))
    monkeypatch.delenv("NEWSCARD_VOICE")
    with pytest.raises(SystemExit, match="newscard tts samples"):  # 직접 지정한 목소리가 틀려도 같은 안내
        tts.run(str(d), voice="Q1")


def test_normalize_off_sends_text_as_is(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("NEWSCARD_TTS_NORMALIZE", "off")
    sent = []
    monkeypatch.setattr(tts, "make_supertonic_synth", lambda *a, **k: (lambda text, path: (sent.append(text), fake_synth()(text, path))))
    d = tmp_path / "data"
    d.mkdir()
    (d / "script.json").write_text(json.dumps([card(1, narration="SNS에서 9천 세대가 화제입니다.").model_dump()], ensure_ascii=False), encoding="utf-8")
    tts.run(str(d))
    assert sent == ["SNS에서 9천 세대가 화제입니다."]  # 한글 변환 없이 원문 그대로 (엔진이 직접 읽는지 비교할 때 사용)


def test_voice_samples_for_all_ten_voices(tmp_path):
    FakeSupertonic.instances.clear()
    res = tts.run_samples(str(tmp_path), tts_factory=factory())
    assert [r["voice"] for r in res] == tts.SUPERTONIC_VOICES and all(not r["error"] for r in res)
    assert len(FakeSupertonic.instances) == 1  # 모델은 한 번만 불러온다
    assert all((tmp_path / "voice_samples" / f"{v}.wav").exists() for v in tts.SUPERTONIC_VOICES)
    assert "구천" in FakeSupertonic.instances[0].calls[0]["text"]  # 숫자 읽기 변환이 적용된 문장


def test_missing_supertonic_package_gives_rebuild_hint(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "supertonic":
            raise ModuleNotFoundError("No module named 'supertonic'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(tts.VoiceMissing, match="docker compose build"):
        tts.load_supertonic(tmp_path)
    with pytest.raises(SystemExit, match="docker compose build"):  # samples 명령도 같은 안내를 한다
        tts.run_samples(str(tmp_path))


# ---- 목소리 선택 (기본 F2, 바꿀 수 있음) ----
def test_default_voice_is_f2(monkeypatch):
    clear_env(monkeypatch)
    assert tts.DEFAULT_VOICE == "F2" and tts.voice_name() == "F2"


def test_voice_priority_argument_over_env_over_default(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("NEWSCARD_VOICE", "M3")
    assert tts.voice_name() == "M3"                # .env 설정
    assert tts.voice_name("m5") == "M5"            # 직접 지정(--voice)이 우선, 대소문자 무관
    monkeypatch.setenv("NEWSCARD_VOICE", "")       # docker compose가 빈 값으로 넘겨도 '설정 안 함'
    assert tts.voice_name() == "F2"


def test_voice_and_number_validation(monkeypatch):
    clear_env(monkeypatch)
    with pytest.raises(ValueError, match="newscard tts samples"):
        tts.voice_name("Z9")
    monkeypatch.setenv("NEWSCARD_VOICE", "ko_KR-kss-medium")  # 예전(Piper) 설정이 남아 있는 경우
    with pytest.raises(ValueError, match="사용 가능: F1"):
        tts.voice_name()
    monkeypatch.setenv("NEWSCARD_TTS_SPEED", "빠르게")
    with pytest.raises(ValueError, match="숫자"):
        tts._float_env("NEWSCARD_TTS_SPEED")
    assert set(tts.SUPERTONIC_VOICES) == {f"{g}{n}" for g in "FM" for n in range(1, 6)}


def test_run_uses_voice_argument_and_records_it(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("NEWSCARD_VOICE", "M4")
    seen = []
    monkeypatch.setattr(tts, "make_supertonic_synth", lambda model_dir, voice, speed=None, steps=None, tts_factory=None: (seen.append(voice), fake_synth())[1])
    d = tmp_path / "data"
    d.mkdir()
    (d / "script.json").write_text(json.dumps([card(1).model_dump()], ensure_ascii=False), encoding="utf-8")
    assert tts.run(str(d))[0]["voice"] == "M4"            # 환경변수
    assert tts.run(str(d), voice="f1")[0]["voice"] == "F1"  # 이번 한 번만 바꾸기
    assert seen == ["M4", "F1"]
    assert json.loads((d / "audio.json").read_text(encoding="utf-8"))[0]["voice"] == "F1"  # 마지막 실행 기준으로 기록


def test_changing_voice_regenerates_audio_files(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    sizes = {}
    monkeypatch.setattr(tts, "make_supertonic_synth", lambda model_dir, voice, speed=None, steps=None: fake_synth(seconds_per_char=0.1 if voice == "F2" else 0.2))
    d = tmp_path / "data"
    d.mkdir()
    (d / "script.json").write_text(json.dumps([card(1).model_dump()], ensure_ascii=False), encoding="utf-8")
    for v in ("F2", "M2"):
        sizes[v] = tts.run(str(d), voice=v)[0]["duration_sec"]
    assert sizes["M2"] > sizes["F2"]  # 두 번째 실행이 첫 번째 파일을 새 목소리로 덮어썼다
