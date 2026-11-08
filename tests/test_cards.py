"""카드 이미지 테스트. 한글 폰트가 없는 환경에서는 그리기 테스트를 건너뛴다."""
import json

import pytest
from PIL import Image, ImageChops

from newscard import cards as C
from newscard.models import ScriptCard

try:
    FONTS = C.Fonts(*C.find_fonts())
except C.FontNotFound:
    FONTS = None
needs_font = pytest.mark.skipif(FONTS is None, reason="한글 폰트를 찾지 못함")


def card(kind="news", rank=1, title="마곡동 난방 중단", bullets=("열수송관 파열로 난방이 중단됐다", "원인은 조사 중이다"), index=1,
         source="출처: 연합뉴스"):
    return ScriptCard(index=index, kind=kind, rank=rank, title=title, bullets=list(bullets),
                      narration="나레이션입니다. " * 5, est_sec=8.0, source_line=source)


# ---- 폰트 ----
def test_font_env_missing_path_gives_clear_error(monkeypatch):
    monkeypatch.setenv("NEWSCARD_FONT", "/없는/경로.ttf")
    with pytest.raises(C.FontNotFound, match="NEWSCARD_FONT"):
        C.find_fonts()


def test_no_font_anywhere_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("NEWSCARD_FONT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(C, "REGULAR_CANDIDATES", [])
    monkeypatch.setattr(C, "BOLD_CANDIDATES", [])
    with pytest.raises(C.FontNotFound, match="assets/fonts"):
        C.find_fonts()


# ---- 테마(시안의 색) ----
def test_themes_match_the_design_colors():
    light, dark = C.THEMES["light"], C.THEMES["dark"]
    assert light.bg == (0xFA, 0xFA, 0xF8) and dark.bg == (0x11, 0x1A, 0x33)
    assert light.accent == (0x33, 0x55, 0xCC) and dark.accent == (0x86, 0xA0, 0xF5)
    assert light.title == (0x18, 0x1C, 0x22) and dark.title == (0xF5, 0xF5, 0xFA)


def test_clean_source():
    assert C.clean_source("출처: 연합뉴스 외 2곳 보도") == "연합뉴스 외 2곳 보도"
    assert C.clean_source("") == ""


# ---- 줄바꿈 ----
@needs_font
def test_wrap_respects_width_and_splits_long_words():
    f = FONTS.get("regular", 50)
    lines = C.wrap("가" * 80, f, 400)
    assert len(lines) > 1 and all(f.getlength(x) <= 400 for x in lines)
    lines = C.wrap("조작기소 특검 도입 공방이 이어지는 가운데 대통령이 신중한 입장", f, 700)
    assert all(f.getlength(x) <= 700 for x in lines) and "".join(lines).replace(" ", "") == \
        "조작기소특검도입공방이이어지는가운데대통령이신중한입장"  # 글자가 빠지거나 늘지 않음


@needs_font
def test_fit_text_shrinks_then_ellipsizes():
    lines, size = C.fit_text("짧은 제목", FONTS, "bold", 800, 2, range(100, 55, -4))
    assert size == 100 and lines == ["짧은 제목"]
    long = "아주 긴 제목입니다 " * 30
    lines, size = C.fit_text(long, FONTS, "bold", 800, 3, range(100, 55, -4))
    assert len(lines) == 3 and lines[-1].endswith("…") and size == 56


# ---- 배치 ----
@needs_font
def test_layout_news_card_has_rank_number_and_notice():
    lay = C.layout_card(card(rank=3), FONTS)
    assert lay.big_number == "03" and lay.label == "TODAY'S NEWS" and lay.notice
    assert lay.source_text == "연합뉴스"
    assert lay.body_size == 46 and lay.dropped_bullets == 0  # 시안 크기 그대로


@needs_font
def test_layout_intro_outro_have_no_number_or_source():
    intro = C.layout_card(card(kind="intro", rank=0, bullets=["2026년 11월 8일"], source=""), FONTS)
    outro = C.layout_card(card(kind="outro", rank=0, source=""), FONTS)
    assert intro.big_number == "" and intro.label == "TODAY'S NEWS" and intro.notice == ""
    assert outro.label == "THANK YOU" and outro.source_text == ""


@needs_font
def test_layout_keeps_body_above_bottom_line():
    long_bullets = ["조작기소 특검 도입을 둘러싼 공방이 이어지는 가운데 대통령이 신중한 입장을 밝혔다는 보도"] * 6
    lay = C.layout_card(card(title="이재명 대통령, 조작기소 특검 놓고 난제에 직면했다는 분석", bullets=long_bullets), FONTS)
    assert lay.bottom_y <= C.BODY_BOTTOM
    assert lay.dropped_bullets > 0 and len(lay.title_lines) <= 3


@needs_font
def test_body_size_stays_at_design_size_when_it_fits():
    a = C.layout_card(card(bullets=["짧은 줄"]), FONTS)
    b = C.layout_card(card(bullets=["짧은 줄", "또 짧은 줄", "세 번째 줄"]), FONTS)
    assert a.body_size == b.body_size == 46


@needs_font
def test_empty_bullets_and_missing_source_do_not_crash():
    lay = C.layout_card(card(bullets=[" ", ""], source=""), FONTS)
    assert lay.body_lines == [] and lay.source_text == ""
    img, _ = C.render_card(card(bullets=[], source=""), FONTS)
    assert img.size == (C.W, C.H)


# ---- 그리기 ----
@needs_font
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_render_uses_theme_background_and_draws_text(theme):
    img, _ = C.render_card(card(), FONTS, C.THEMES[theme], 5, "2026. 11. 08")
    assert img.size == (1080, 1920) and img.getpixel((5, 5)) == C.THEMES[theme].bg
    blank, _ = C.render_card(card(title="가", bullets=[], source=""), FONTS, C.THEMES[theme], 5, "2026. 11. 08")
    assert ImageChops.difference(img, blank).getbbox() is not None


@needs_font
def test_light_and_dark_differ_only_in_color():
    a, la = C.render_card(card(), FONTS, C.THEMES["light"], 5, "2026. 11. 08")
    b, lb = C.render_card(card(), FONTS, C.THEMES["dark"], 5, "2026. 11. 08")
    assert la == lb and a.size == b.size  # 배치는 같고
    assert ImageChops.difference(a, b).getbbox() is not None  # 색만 다르다


@needs_font
def test_long_source_is_ellipsized_not_overlapping_date():
    img, lay = C.render_card(card(source="출처: " + "아주긴언론사이름" * 10), FONTS, C.THEMES["light"], 5, "2026. 11. 08")
    assert img.size == (1080, 1920)


@needs_font
def test_render_all_writes_both_themes_and_manifest(tmp_path):
    out = tmp_path / "cards"
    (out / "light").mkdir(parents=True)
    (out / "light" / "card_99_old.png").write_bytes(b"old")  # 예전 실행의 파일은 지워진다
    cs = [card(kind="intro", index=0, rank=0, title="오늘의 주요 뉴스 Top 1", bullets=["2026년 11월 8일"], source=""),
          card(index=1), card(kind="outro", index=2, rank=0, title="시청해 주셔서 감사합니다", bullets=["AI 요약"], source="")]
    res = C.render_all(cs, str(out), fonts=FONTS, date_text="2026. 11. 08")
    assert set(res) == {"light", "dark"} and not (out / "light" / "card_99_old.png").exists()
    for theme, m in res.items():
        assert [x["index"] for x in m] == [0, 1, 2] and all(x["theme"] == theme for x in m)
        for x in m:
            with Image.open(x["image"]) as im:
                assert im.size == (1080, 1920) and im.format == "PNG"
    assert res["dark"][1]["image"].endswith("dark/card_01_news_1.png") and res["dark"][1]["narration"]


@needs_font
def test_render_all_single_theme(tmp_path):
    res = C.render_all([card()], str(tmp_path / "c"), fonts=FONTS, themes=("dark",))
    assert list(res) == ["dark"] and not (tmp_path / "c" / "light").exists()


def test_run_requires_script(tmp_path):
    with pytest.raises(SystemExit, match="newscard script"):
        C.run(str(tmp_path))


@needs_font
def test_run_writes_cards_json(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "script.json").write_text(json.dumps([card(index=0).model_dump()], ensure_ascii=False), encoding="utf-8")
    res = C.run(str(d), themes=("light",))
    assert json.loads((d / "cards.json").read_text(encoding="utf-8")).keys() == {"light"}
    assert len(res["light"]) == 1 and (d / "cards" / "light" / "card_00_news_1.png").exists()
