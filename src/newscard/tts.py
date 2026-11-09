"""대본(script.json)의 나레이션을 음성(WAV)으로 만든다.

- 음성 엔진: Supertonic 3 (ONNX, CPU에서 실행). 목소리 F1~F5(여), M1~M5(남) 중 선택하며 기본은 F2.
  처음 한 번 data/voices/ 로 모델(약 400MB)을 내려받는다.
- 목소리는 NEWSCARD_VOICE 환경변수나 `newscard tts --voice M2` 로 바꿀 수 있다.
- 숫자/날짜/단위는 읽는 방식 그대로 한글로 바꿔서(normalize_for_tts) 음성 엔진에 준다. ("11월 8일" -> "십일월 팔일")
- 카드마다 data/audio/card_00_intro.wav 를 만들고, 실제 음성 길이를 data/audio.json 에 기록한다.
  영상 단계는 이 실제 길이로 카드 표시 시간을 정한다. (대본의 est_sec는 글자 수 기반 추정일 뿐)
"""
import json
import logging
import os
import re
import wave
from pathlib import Path
from typing import Callable

from newscard.models import ScriptCard

log = logging.getLogger(__name__)

MAX_CARD_SEC = 35.0  # 카드 한 장의 음성이 이보다 길면 알림

# ---------------------------------------------------------------- 숫자 읽기
_SINO = "영일이삼사오육칠팔구"
_NATIVE_ONES = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯", 7: "일곱", 8: "여덟", 9: "아홉"}
_NATIVE_TENS = {1: "열", 2: "스물", 3: "서른", 4: "마흔", 5: "쉰", 6: "예순", 7: "일흔", 8: "여든", 9: "아흔"}
_COUNTERS = r"(?:가지|명|개|곳|마리|살|장|채|시)"  # 앞의 숫자를 '하나, 둘'처럼 읽는 단위


def sino(n: int) -> str:
    """한자어 수(일, 이, 삼 ...). 맞춤법대로 만/억/조 단위마다 띄어 쓴다. (12345 -> 만 이천삼백사십오)"""
    if n == 0:
        return "영"
    parts = []
    for unit, name in ((10**12, "조"), (10**8, "억"), (10**4, "만")):
        if n >= unit:
            head, n = divmod(n, unit)
            if head:
                # '만'만 있을 때는 '일'을 빼고 읽는다(만, 만 이천). 앞에 억/조가 있으면 '일만'
                bare = name == "만" and head == 1 and not parts
                parts.append(("" if bare else _below_10000(head)) + name)
    if n:
        parts.append(_below_10000(n))
    return " ".join(parts)


def _below_10000(n: int) -> str:
    out = ""
    for unit, name in ((1000, "천"), (100, "백"), (10, "십")):
        d, n = divmod(n, unit)
        if d:
            out += ("" if d == 1 else _SINO[d]) + name
    if n:
        out += _SINO[n]
    return out


def native(n: int) -> str:
    """고유어 수(하나, 둘 -> 관형형 한, 두, 세 ...). 1~99만 지원한다."""
    tens, ones = divmod(n, 10)
    if n == 20:
        return "스무"
    return _NATIVE_TENS.get(tens, "") + _NATIVE_ONES.get(ones, "")


# ---------------------------------------------------------------- 영어 읽기
# 한국어 음성 엔진은 영어 글자를 어색하게 읽는다. 읽기 전에 한글 발음으로 바꾼다.
_LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [
    "에이", "비", "씨", "디", "이", "에프", "지", "에이치", "아이", "제이", "케이", "엘", "엠",
    "엔", "오", "피", "큐", "알", "에스", "티", "유", "브이", "더블유", "엑스", "와이", "제트"]))

DEFAULT_PRONUNCIATIONS = {
    "SNS": "에스엔에스", "AI": "에이아이", "IT": "아이티", "TV": "티브이", "PC": "피씨", "CEO": "씨이오",
    "KBS": "케이비에스", "MBC": "엠비씨", "SBS": "에스비에스", "EBS": "이비에스", "YTN": "와이티엔",
    "JTBC": "제이티비씨", "BBC": "비비씨", "CNN": "씨엔엔", "FT": "에프티",
    "GDP": "지디피", "ETF": "이티에프", "IPO": "아이피오", "FTA": "에프티에이", "IMF": "아이엠에프",
    "UN": "유엔", "EU": "이유", "US": "유에스", "USA": "유에스에이", "NATO": "나토", "FBI": "에프비아이",
    "CIA": "씨아이에이", "LTE": "엘티이", "GPT": "지피티", "ChatGPT": "챗지피티", "OpenAI": "오픈에이아이",
    "iPhone": "아이폰", "YouTube": "유튜브", "Google": "구글", "Apple": "애플", "UFC": "유에프씨",
    "K": "케이", "LG": "엘지", "SK": "에스케이", "KT": "케이티", "KAIST": "카이스트", "GLP": "지엘피",
}
_GENERATION = {"3": "쓰리지", "4": "포지", "5": "파이브지", "6": "식스지"}  # 5G -> 파이브지
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9&]*")


def load_pronunciations(path: str = "config/pronunciations.yaml") -> dict[str, str]:
    """기본 사전 + 사용자 사전(config/pronunciations.yaml). 사용자 사전이 우선한다."""
    table = dict(DEFAULT_PRONUNCIATIONS)
    p = Path(path)
    if p.exists():
        import yaml

        table.update({str(k): str(v) for k, v in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).items()})
    return table


def read_english(text: str, table: dict[str, str] | None = None, unknown: list[str] | None = None) -> str:
    """영어 단어를 한글 발음으로 바꾼다.
    1) 사전에 있으면 사전대로  2) 모르는 대문자 약어(2~6자)는 글자 이름대로(예: ABC -> 에이비씨)
    3) 그 밖의 영어 단어는 그대로 두고 unknown 목록에 담아 알려 준다."""
    table = table if table is not None else DEFAULT_PRONUNCIATIONS
    lookup = {k.casefold(): v for k, v in table.items()}
    text = re.sub(r"(?<![\w])([3-6])G(?![A-Za-z0-9])", lambda m: _GENERATION[m.group(1)], text)

    def repl(m: re.Match) -> str:
        w = m.group(0)
        hit = lookup.get(w.casefold())
        if hit is not None:
            return hit
        if w.isupper() and w.isalpha() and 1 <= len(w) <= 6:
            return "".join(_LETTERS[c] for c in w)
        if unknown is not None and w not in unknown:
            unknown.append(w)
        return w

    return _WORD.sub(repl, text)


_NUM = re.compile(r"(\d[\d,]*)(?:\.(\d+))?")


def _read_number(m: re.Match, counter_follows: bool) -> str:
    whole = int(m.group(1).replace(",", ""))
    frac = m.group(2)
    if frac:
        return sino(whole) + " 점 " + "".join(_SINO[int(c)] for c in frac)
    if counter_follows and 1 <= whole <= 99:
        return native(whole)
    return sino(whole)


def normalize_for_tts(text: str, table: dict[str, str] | None = None, unknown: list[str] | None = None) -> str:
    """음성 엔진이 숫자와 영어를 엉뚱하게 읽지 않도록, 한글 발음으로 바꾼다."""
    text = re.sub(r"[“”\"‘’']", "", text)  # 따옴표는 읽지 않는다
    text = read_english(text, table, unknown)  # 영어는 숫자보다 먼저 (5G -> 파이브지)
    text = re.sub(r"(\d)\s*%", r"\1 퍼센트", text)
    # 월: 6월 -> 유월, 10월 -> 시월 (예외 읽기)
    text = re.sub(r"(?<!\d)(\d{1,2})월", lambda m: {6: "유월", 10: "시월"}.get(int(m.group(1)), sino(int(m.group(1))) + " 월"), text)

    def repl(m: re.Match) -> str:
        after = text[m.end():]
        counter = bool(re.match(rf"\s?{_COUNTERS}", after))
        return _read_number(m, counter)

    out, last = [], 0
    for m in _NUM.finditer(text):
        out.append(text[last:m.start()])
        out.append(repl(m))
        nxt = text[m.end():m.end() + 1]
        # 숫자 바로 뒤에 한글 단위가 붙어 있으면 띄어 쓴다. (다섯 가지, 스무 명, 이천이십육 년)
        # 천/만/억/조는 수의 일부라서 붙여 쓴다. (구천)
        if nxt and (("가" <= nxt <= "힣" and nxt not in "천만억조") or nxt.isascii() and nxt.isalpha()):
            out.append(" ")
        last = m.end()
    out.append(text[last:])
    return re.sub(r"\s+", " ", "".join(out)).strip()


# ---------------------------------------------------------------- 음성 엔진 (Supertonic 3)
Synth = Callable[[str, Path], None]  # (읽을 글, 저장할 wav 경로). 테스트에서는 가짜 함수를 넣는다.

SUPERTONIC_MODEL = "supertonic-3"
SUPERTONIC_VOICES = ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"]  # 여성 5, 남성 5
DEFAULT_VOICE = "F2"
SAMPLE_TEXT = "오늘의 주요 뉴스를 전해드립니다. 서울 마곡동에서 열수송관이 터져 약 구천 세대의 난방과 온수가 끊겼습니다."


class VoiceMissing(RuntimeError):
    pass


def _env(name: str) -> str:
    """환경변수를 읽는다. docker compose가 빈 값("")으로 넘겨도 '설정 안 함'으로 본다."""
    return (os.environ.get(name) or "").strip()


def voice_name(voice: str | None = None) -> str:
    """쓸 목소리. 우선순위: 직접 지정(--voice) > NEWSCARD_VOICE > 기본값(F2). 대소문자는 구분하지 않는다."""
    chosen = (voice or _env("NEWSCARD_VOICE") or DEFAULT_VOICE).strip().upper()
    if chosen not in SUPERTONIC_VOICES:
        raise ValueError(
            f"목소리 '{chosen}'은(는) 사용할 수 없어요. 사용 가능: {', '.join(SUPERTONIC_VOICES)} "
            f"(`newscard tts samples`로 들어 볼 수 있어요)"
        )
    return chosen


def _float_env(name: str) -> float | None:
    raw = _env(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name}={raw} 은(는) 숫자가 아니에요.")


def drop_unsupported(text: str, processor) -> tuple[str, list[str]]:
    """모델이 읽지 못하는 글자를 뺀다. (빠진 글자 목록도 돌려준다)"""
    ok, bad = processor.validate_text(text)
    if ok:
        return text, []
    cleaned = "".join(c for c in text if c not in set(bad))
    return re.sub(r"\s+", " ", cleaned).strip(), list(bad)


def load_supertonic(model_dir: Path, tts_factory=None):
    """Supertonic 3 모델을 불러온다. 없으면 model_dir로 내려받는다(약 400MB, 처음 한 번)."""
    if tts_factory is None:
        try:
            from supertonic import TTS as tts_factory  # 무거운 라이브러리라 필요할 때만 불러온다
        except ImportError as e:
            raise VoiceMissing(
                "supertonic 패키지가 설치돼 있지 않아요. Docker 이미지를 다시 만들어야 해요: `docker compose build`\n"
                f"(내 PC에서 직접 실행한다면 `pip install -e \".[dev]\"`)  원인: {e}"
            )
    log.info("Supertonic 3 모델 준비 중... (처음에는 약 400MB를 내려받아요)")
    try:
        return tts_factory(model=SUPERTONIC_MODEL, model_dir=str(model_dir), auto_download=True)
    except Exception as e:
        raise VoiceMissing(
            f"Supertonic 모델을 준비하지 못했어요. 인터넷 연결과 디스크 공간(약 400MB)을 확인하세요.\n{type(e).__name__}: {e}"
        )


def supertonic_synth(tts, voice: str, speed: float | None = None, steps: int | None = None, lang: str = "ko") -> Synth:
    try:
        style = tts.get_voice_style(voice)
    except Exception as e:
        raise VoiceMissing(f"목소리 '{voice}'를 불러오지 못했어요: {type(e).__name__}: {e}")
    options = {}
    if speed:
        options["speed"] = speed
    if steps:
        options["total_steps"] = steps

    def synth(text: str, path: Path) -> None:
        cleaned, dropped = drop_unsupported(text, tts.model.text_processor)
        if dropped:
            log.warning("모델이 읽지 못하는 글자를 뺐어요: %s", "".join(dropped))
        if not cleaned:
            raise ValueError("읽을 수 있는 글자가 없음")
        wav, _ = tts.synthesize(cleaned, voice_style=style, lang=lang, **options)
        path.parent.mkdir(parents=True, exist_ok=True)
        tts.save_audio(wav, str(path))

    return synth


def make_supertonic_synth(model_dir: Path, voice: str, speed: float | None = None, steps: int | None = None,
                          tts_factory=None) -> Synth:
    return supertonic_synth(load_supertonic(model_dir, tts_factory), voice, speed, steps)


def build_synth(voice: str, data_dir: str) -> Synth:
    """환경변수(속도, 합성 단계)와 고른 목소리로 합성 함수를 만든다."""
    steps = _env("NEWSCARD_TTS_STEPS")
    return make_supertonic_synth(
        Path(data_dir) / "voices" / "supertonic3", voice, _float_env("NEWSCARD_TTS_SPEED"),
        int(steps) if steps else None,
    )


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / float(w.getframerate()), 2)


# ---------------------------------------------------------------- 카드별 음성
def synthesize_cards(cards: list[ScriptCard], synth: Synth, out_dir: Path,
                     pronunciations: dict[str, str] | None = None, normalize: bool = True) -> list[dict]:
    """카드마다 음성 파일을 만든다. 한 카드가 실패해도 나머지는 계속한다."""
    table = pronunciations if pronunciations is not None else load_pronunciations()
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("card_*.wav"):
        old.unlink()
    items = []
    for c in cards:
        suffix = f"_{c.rank}" if c.kind == "news" else ""
        path = out_dir / f"card_{c.index:02d}_{c.kind}{suffix}.wav"
        unknown: list[str] = []
        spoken = normalize_for_tts(c.narration, table, unknown) if normalize else re.sub(r"\s+", " ", c.narration).strip()
        item = {"index": c.index, "kind": c.kind, "rank": c.rank, "audio": str(path).replace("\\", "/"),
                "narration": c.narration, "spoken_text": spoken, "est_sec": c.est_sec,
                "duration_sec": 0.0, "status": "ok", "error": "",
                "warnings": [f"영어 '{w}'의 발음을 몰라 그대로 읽습니다 (config/pronunciations.yaml에 추가하세요)" for w in unknown]}
        try:
            if not spoken:
                raise ValueError("읽을 나레이션이 비어 있음")
            synth(spoken, path)
            item["duration_sec"] = wav_seconds(path)
            if item["duration_sec"] <= 0.3:
                raise ValueError(f"생성된 음성이 너무 짧음({item['duration_sec']}초)")
            if item["duration_sec"] > MAX_CARD_SEC:
                item["warnings"].append(f"음성이 {item['duration_sec']}초로 깁니다 (기준 {MAX_CARD_SEC:.0f}초)")
        except Exception as e:  # 한 카드 실패가 전체를 멈추지 않게
            item["status"], item["error"] = "failed", f"{type(e).__name__}: {e}"
            log.warning("[%d] 음성 생성 실패: %s", c.index, item["error"])
            path.unlink(missing_ok=True)
        items.append(item)
    return items


def run(data_dir: str = "data", voice: str | None = None) -> list[dict]:
    """나레이션을 음성으로 만든다. voice로 이번 실행의 목소리를 바꿀 수 있다 (예: "M2")."""
    src = Path(data_dir) / "script.json"
    if not src.exists():
        raise SystemExit("data/script.json 이 없어요. 먼저 `newscard script`를 실행하세요.")
    cards = [ScriptCard.model_validate(x) for x in json.loads(src.read_text(encoding="utf-8"))]
    if not cards:
        raise SystemExit("대본이 비어 있어요.")
    try:
        chosen = voice_name(voice)
        synth = build_synth(chosen, data_dir)
    except (VoiceMissing, ValueError) as e:
        raise SystemExit(str(e))
    normalize = _env("NEWSCARD_TTS_NORMALIZE").lower() not in ("off", "0", "false", "no")
    items = synthesize_cards(cards, synth, Path(data_dir) / "audio", normalize=normalize)
    for i in items:
        i.update(engine=SUPERTONIC_MODEL, voice=chosen)
    (Path(data_dir) / "audio.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def run_samples(data_dir: str = "data", tts_factory=None) -> list[dict]:
    """Supertonic의 10가지 목소리로 같은 문장을 읽어서 data/voice_samples/ 에 저장한다. (목소리를 고를 때 사용)"""
    out = Path(data_dir) / "voice_samples"
    out.mkdir(parents=True, exist_ok=True)
    try:
        tts = load_supertonic(Path(data_dir) / "voices" / "supertonic3", tts_factory)
    except VoiceMissing as e:
        raise SystemExit(str(e))
    text = normalize_for_tts(SAMPLE_TEXT)
    steps = _env("NEWSCARD_TTS_STEPS")
    results = []
    for v in SUPERTONIC_VOICES:
        path = out / f"{v}.wav"
        try:
            supertonic_synth(tts, v, _float_env("NEWSCARD_TTS_SPEED"), int(steps) if steps else None)(text, path)
            results.append({"voice": v, "path": str(path).replace("\\", "/"), "duration_sec": wav_seconds(path), "error": ""})
        except Exception as e:
            results.append({"voice": v, "path": "", "duration_sec": 0.0, "error": f"{type(e).__name__}: {e}"})
    return results
