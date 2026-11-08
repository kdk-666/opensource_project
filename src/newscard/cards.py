"""대본(script.json)을 카드뉴스 이미지(PNG)로 그린다. Pillow만 사용한다.

디자인 시안(밝은 모드/다크 모드)을 따른다.
- 1080x1920 (9:16), 좌측 정렬. 헤더 / 구분선 / 라벨+큰 순위 숫자 / 제목 / 포인트 바 / 본문 / 구분선 / 출처+날짜.
- 밝은 모드와 다크 모드는 색만 다르다.
- 글이 길면 글자 크기를 줄이고, 그래도 넘치면 본문 줄을 뒤에서부터 뺀다. (글이 영역 밖으로 나가지 않게 한다)
- 한글 폰트는 자동으로 찾는다: NEWSCARD_FONT 환경변수 > assets/fonts/ > 시스템(Nanum, 맑은 고딕, Noto CJK 등)
"""
import glob
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from newscard.models import ScriptCard

log = logging.getLogger(__name__)

W, H = 1080, 1920
MX = 108  # 좌우 여백
BRAND = os.environ.get("NEWSCARD_BRAND", "THE DAILY BRIEF")
KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Theme:
    name: str
    bg: tuple
    brand: tuple
    top_label: tuple  # 'TOP 5'
    line: tuple
    accent: tuple  # 'TODAY'S NEWS' 와 포인트 바
    number: tuple  # 큰 순위 숫자
    title: tuple
    body: tuple
    source_label: tuple
    source: tuple
    date: tuple
    notice: tuple  # AI 요약 안내


def _rgb(h: str) -> tuple:
    return tuple(int(h[i : i + 2], 16) for i in (1, 3, 5))


THEMES = {
    "light": Theme("light", _rgb("#FAFAF8"), _rgb("#181C22"), _rgb("#7B8190"), _rgb("#DADCE0"), _rgb("#3355CC"),
                   _rgb("#8A8F9A"), _rgb("#181C22"), _rgb("#4F5563"), _rgb("#7B8190"), _rgb("#181C22"),
                   _rgb("#7A7F8A"), _rgb("#7B8190")),
    "dark": Theme("dark", _rgb("#111A33"), _rgb("#FFFFFF"), _rgb("#A9B4CE"), _rgb("#3A435A"), _rgb("#86A0F5"),
                  _rgb("#A3B1CF"), _rgb("#F5F5FA"), _rgb("#D5DAE8"), _rgb("#A9B4CE"), _rgb("#FFFFFF"),
                  _rgb("#A9B4CE"), _rgb("#A9B4CE")),
}

# 시안(882x1568)을 1080x1920으로 환산한 위치 (y는 글자 세로 중심 또는 위쪽 기준)
HEADER_Y = 256
LINE_TOP_Y = 301
LABEL_Y = 413
TITLE_BASELINE = 705  # 제목 첫 줄의 기준선 (시안: 100px 제목의 첫 줄 중심 y≈677)
BODY_BOTTOM = 1340  # 본문이 이 아래로 내려가면 안 된다
LINE_BOTTOM_Y = 1391
SOURCE_LABEL_Y = 1440
SOURCE_Y = 1496
NOTICE_Y = 1556

REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]


class FontNotFound(RuntimeError):
    pass


def find_fonts() -> tuple[str, str]:
    """(일반 폰트, 굵은 폰트) 경로. 굵은 글꼴이 없으면 일반 글꼴을 쓴다."""
    env = os.environ.get("NEWSCARD_FONT")
    if env:
        if not Path(env).exists():
            raise FontNotFound(f"NEWSCARD_FONT 경로에 파일이 없어요: {env}")
        return env, os.environ.get("NEWSCARD_FONT_BOLD", env)
    local = sorted(glob.glob("assets/fonts/*.ttf") + glob.glob("assets/fonts/*.otf") + glob.glob("assets/fonts/*.ttc"))
    bold_local = [p for p in local if "bold" in Path(p).name.lower()]
    reg_local = [p for p in local if p not in bold_local]
    regular = (reg_local or [None])[0] or next((p for p in REGULAR_CANDIDATES if Path(p).exists()), None)
    bold = (bold_local or [None])[0] or next((p for p in BOLD_CANDIDATES if Path(p).exists()), None) or regular
    if not regular:
        raise FontNotFound(
            "한글 폰트를 찾지 못했어요. Docker에서는 fonts-nanum이 설치돼 있어야 하고, "
            "내 PC에서 직접 실행한다면 assets/fonts/ 에 폰트(.ttf)를 넣거나 NEWSCARD_FONT 환경변수를 지정하세요."
        )
    return regular, bold


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """폰트를 연다. .ttc(여러 글꼴 묶음)는 한국어(KR) 글꼴을 골라서 연다."""
    if path.lower().endswith(".ttc"):
        for i in range(12):
            try:
                f = ImageFont.truetype(path, size, index=i)
            except OSError:
                break
            if "KR" in " ".join(f.getname()):
                return f
    return ImageFont.truetype(path, size)


@dataclass
class Fonts:
    regular: str
    bold: str
    _cache: dict = field(default_factory=dict)

    def get(self, kind: str, size: int) -> ImageFont.FreeTypeFont:
        key = (kind, size)
        if key not in self._cache:
            self._cache[key] = load_font(self.bold if kind == "bold" else self.regular, size)
        return self._cache[key]


def _greedy(words: list[str], font, max_width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= max_width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        for ch in word:  # 한 단어가 한 줄보다 길면 글자 단위로 자른다
            if font.getlength(cur + ch) <= max_width:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    return lines


def _balanced(words: list[str], font, max_width: int, k: int) -> list[str] | None:
    """단어를 정확히 k줄로 나누되 줄 길이가 고르게 되도록 한다. (한 줄에 단어 하나만 남는 줄을 줄임)"""
    n = len(words)
    width = {}

    def w(i: int, j: int) -> float:
        if (i, j) not in width:
            width[(i, j)] = font.getlength(" ".join(words[i:j]))
        return width[(i, j)]

    INF = float("inf")
    cost = [[INF] * (n + 1) for _ in range(k + 1)]
    back = [[0] * (n + 1) for _ in range(k + 1)]
    cost[0][0] = 0.0
    for line in range(1, k + 1):
        for j in range(line, n + 1):
            for i in range(line - 1, j):
                if cost[line - 1][i] == INF or w(i, j) > max_width:
                    continue
                c = cost[line - 1][i] + (max_width - w(i, j)) ** 2
                if c < cost[line][j]:
                    cost[line][j], back[line][j] = c, i
    if cost[k][n] == INF:
        return None
    out, j = [], n
    for line in range(k, 0, -1):
        i = back[line][j]
        out.append(" ".join(words[i:j]))
        j = i
    return out[::-1]


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """픽셀 너비 기준으로 줄바꿈한다. 줄 수는 그리디 방식과 같고, 줄 길이는 최대한 고르게 맞춘다."""
    words = text.split()
    lines = _greedy(words, font, max_width)
    if 2 <= len(lines) <= 6 and all(font.getlength(x) <= max_width for x in words):
        balanced = _balanced(words, font, max_width, len(lines))
        if balanced:
            return balanced
    return lines


def fit_text(text: str, fonts: Fonts, kind: str, max_width: int, max_lines: int,
             sizes: range) -> tuple[list[str], int]:
    """max_lines 안에 들어가는 가장 큰 글자 크기를 찾는다. 안 되면 가장 작은 크기로 말줄임한다."""
    for size in sizes:
        lines = wrap(text, fonts.get(kind, size), max_width)
        if len(lines) <= max_lines:
            return lines, size
    size = sizes[-1]
    lines = wrap(text, fonts.get(kind, size), max_width)[:max_lines]
    if lines:
        last = lines[-1]
        while last and fonts.get(kind, size).getlength(last + "…") > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines, size


@dataclass
class Layout:
    """카드 한 장의 배치 결과. 그리기 전에 만들어서 넘침을 검사할 수 있다."""

    label: str = ""
    big_number: str = ""
    title_lines: list[str] = field(default_factory=list)
    title_size: int = 0
    bar_y: int = 0
    body_lines: list[list[str]] = field(default_factory=list)  # 본문 문단 하나가 여러 줄로 감길 수 있음
    body_size: int = 0
    body_top: int = 0
    dropped_bullets: int = 0
    source_text: str = ""
    notice: str = ""
    bottom_y: int = 0  # 본문이 끝나는 y좌표


def clean_source(line: str) -> str:
    """'출처: 연합뉴스 외 2곳 보도' -> '연합뉴스 외 2곳 보도' (SOURCE 라벨이 따로 있으므로)"""
    return line.replace("출처:", "", 1).strip() if line else ""


def layout_card(card: ScriptCard, fonts: Fonts) -> Layout:
    inner = W - 2 * MX
    lay = Layout()
    if card.kind == "news":
        lay.label, lay.big_number = "TODAY'S NEWS", f"{card.rank:02d}"
        lay.notice = "AI가 기사를 요약한 내용입니다"
    else:
        lay.label = "TODAY'S NEWS" if card.kind == "intro" else "THANK YOU"
    lay.source_text = clean_source(card.source_line)

    lay.title_lines, lay.title_size = fit_text(card.title, fonts, "bold", inner, 3, range(100, 59, -4))
    # 첫 줄의 기준선(baseline)을 고정해서, 제목 글자 크기가 달라도 카드마다 제목 위치가 같게 한다
    title_bottom = TITLE_BASELINE + (len(lay.title_lines) - 1) * int(lay.title_size * 1.38) + int(lay.title_size * 0.3)
    lay.bar_y = title_bottom + 56
    lay.body_top = lay.bar_y + 8 + 36

    paragraphs = [b for b in card.bullets if b.strip()]
    room = BODY_BOTTOM - lay.body_top
    chosen = None
    for size in range(46, 33, -2):  # 시안은 46px. 넘칠 때만 글자 크기를 줄인다
        font = fonts.get("regular", size)
        wrapped = [wrap(p, font, inner) for p in paragraphs]
        total = sum(len(x) * int(size * 1.5) + 22 for x in wrapped)
        if total <= room:
            chosen = (wrapped, size, total)
            break
    if chosen is None:  # 그래도 넘치면 뒤 문단부터 뺀다
        size = 34
        font = fonts.get("regular", size)
        while paragraphs:
            wrapped = [wrap(p, font, inner) for p in paragraphs]
            total = sum(len(x) * int(size * 1.5) + 22 for x in wrapped)
            if total <= room:
                break
            paragraphs = paragraphs[:-1]
            lay.dropped_bullets += 1
        else:
            wrapped, total = [], 0
        chosen = (wrapped, size, total)
    lay.body_lines, lay.body_size, body_h = chosen
    lay.bottom_y = lay.body_top + body_h
    return lay


def render_card(card: ScriptCard, fonts: Fonts, theme: Theme | None = None, n_news: int = 5,
                date_text: str | None = None) -> tuple[Image.Image, Layout]:
    theme = theme or THEMES["light"]
    date_text = date_text or datetime.now(KST).strftime("%Y. %m. %d")
    lay = layout_card(card, fonts)
    img = Image.new("RGB", (W, H), theme.bg)
    d = ImageDraw.Draw(img)
    right = W - MX

    # 헤더: 브랜드(좌) / TOP N(우), 구분선
    d.text((MX, HEADER_Y), BRAND, font=fonts.get("bold", 30), fill=theme.brand, anchor="lm")
    d.text((right, HEADER_Y), f"TOP {n_news}", font=fonts.get("bold", 30), fill=theme.top_label, anchor="rm")
    d.rectangle([MX, LINE_TOP_Y, right, LINE_TOP_Y + 1], fill=theme.line)

    # 라벨(좌) / 큰 순위 숫자(우)
    d.text((MX, LABEL_Y), lay.label, font=fonts.get("bold", 32), fill=theme.accent, anchor="lm")
    if lay.big_number:
        d.text((right, LABEL_Y), lay.big_number, font=fonts.get("bold", 158), fill=theme.number, anchor="rm")

    # 제목
    y = TITLE_BASELINE
    tf = fonts.get("bold", lay.title_size)
    for line in lay.title_lines:
        d.text((MX, y), line, font=tf, fill=theme.title, anchor="ls")
        y += int(lay.title_size * 1.38)

    # 포인트 바
    d.rounded_rectangle([MX, lay.bar_y, MX + 93, lay.bar_y + 8], radius=4, fill=theme.accent)

    # 본문
    bf = fonts.get("regular", lay.body_size)
    y = lay.body_top
    for paragraph in lay.body_lines:
        for line in paragraph:
            d.text((MX, y), line, font=bf, fill=theme.body)
            y += int(lay.body_size * 1.5)
        y += 22

    # 하단: 구분선, SOURCE, 출처(좌) / 날짜(우)
    d.rectangle([MX, LINE_BOTTOM_Y, right, LINE_BOTTOM_Y + 1], fill=theme.line)
    df = fonts.get("regular", 30)
    d.text((right, SOURCE_Y), date_text, font=df, fill=theme.date, anchor="rm")
    if lay.source_text:
        d.text((MX, SOURCE_LABEL_Y), "SOURCE", font=fonts.get("bold", 26), fill=theme.source_label, anchor="lm")
        sf = fonts.get("regular", 33)
        room = right - MX - int(df.getlength(date_text)) - 40
        text = lay.source_text
        while text and sf.getlength(text) > room:
            text = text[:-1]
        if text != lay.source_text:
            text = text[:-1] + "…"
        d.text((MX, SOURCE_Y), text, font=sf, fill=theme.source, anchor="lm")
    if lay.notice:
        d.text((MX, NOTICE_Y), lay.notice, font=fonts.get("regular", 30), fill=theme.notice, anchor="lm")
    return img, lay


def render_all(cards: list[ScriptCard], out_dir: str = "data/cards", fonts: Fonts | None = None,
               themes: tuple[str, ...] = ("light", "dark"), date_text: str | None = None) -> dict[str, list[dict]]:
    """테마별로 카드를 그린다. out_dir/<테마>/card_00_intro.png ... 를 만들고 테마별 목록을 돌려준다."""
    fonts = fonts or Fonts(*find_fonts())
    n_news = sum(1 for c in cards if c.kind == "news")
    result: dict[str, list[dict]] = {}
    for tname in themes:
        out = Path(out_dir) / tname
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("card_*.png"):
            old.unlink()  # 예전 실행의 카드가 섞이지 않게 비운다
        manifest = []
        for c in cards:
            img, lay = render_card(c, fonts, THEMES[tname], n_news, date_text)
            suffix = f"_{c.rank}" if c.kind == "news" else ""
            path = out / f"card_{c.index:02d}_{c.kind}{suffix}.png"
            img.save(path, "PNG")
            if lay.dropped_bullets:
                log.warning("[%s %d] 글이 넘쳐 본문 %d줄을 뺐어요: %s", tname, c.index, lay.dropped_bullets, c.title)
            manifest.append({
                "index": c.index, "kind": c.kind, "rank": c.rank, "theme": tname,
                "image": str(path).replace("\\", "/"), "narration": c.narration, "est_sec": c.est_sec,
                "title": c.title, "dropped_bullets": lay.dropped_bullets, "width": W, "height": H,
            })
        result[tname] = manifest
    return result


def run(data_dir: str = "data", themes: tuple[str, ...] = ("light", "dark")) -> dict[str, list[dict]]:
    src = Path(data_dir) / "script.json"
    if not src.exists():
        raise SystemExit("data/script.json 이 없어요. 먼저 `newscard script`를 실행하세요.")
    cards = [ScriptCard.model_validate(x) for x in json.loads(src.read_text(encoding="utf-8"))]
    if not cards:
        raise SystemExit("대본이 비어 있어요.")
    result = render_all(cards, str(Path(data_dir) / "cards"), themes=themes)
    (Path(data_dir) / "cards.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
