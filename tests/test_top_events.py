"""네트워크 없이 돌아가는 테스트. RSS는 문자열, 본문은 가짜 fetcher로 대체한다."""
import feedparser
import pytest

from newscard.collect import norm_url, parse_feed, parse_related
from newscard.config import Settings
from newscard.cluster import title_similarity
from newscard.models import Article
from newscard.pipeline import build_top_events
from newscard.scoring import rank_score, source_score

MAGOK_A = (
    "서울 강서구 마곡동에서 온수관이 파열되면서 인근 9천여 세대의 난방과 온수 공급이 중단됐다. "
    "서울에너지공사는 7일 새벽 마곡동 일대 열수송관이 파열돼 긴급 복구에 나섰다고 밝혔다. "
    "복구는 오후까지 이어질 전망이며 주민들은 난방 중단으로 불편을 겪었다. 공사 측은 임시 난방기를 지원한다."
)
MAGOK_B = (
    "마곡지구 지역난방 배관 사고로 수천 세대가 추위에 떨었다. 강서구 마곡동 열수송관 파열 사고로 "
    "약 9천 세대의 난방과 온수가 끊겼고 서울에너지공사가 복구 작업을 진행하고 있다. "
    "주민 불편이 이어지자 공사는 임시 난방기를 나눠주고 있다고 전했다."
)
SAMSUNG_EARNINGS = (
    "삼성전자가 7일 3분기 확정 실적을 발표했다. 반도체 부문 영업이익이 크게 늘며 전체 영업이익은 "
    "시장 예상을 웃돌았다. 메모리 가격 상승과 고대역폭메모리 판매 확대가 실적을 이끌었다고 회사는 "
    "설명했다. 매출과 영업이익 모두 분기 기준 역대 최대 수준에 근접했다."
)
SAMSUNG_PHONE = (
    "삼성전자가 7일 신형 스마트폰 갤럭시 신제품을 공개하고 출시 일정을 밝혔다. 새 모델은 인공지능 "
    "기능과 카메라 성능을 강화했으며 사전 예약은 다음 주부터 시작된다. 삼성전자는 국내 출시 후 "
    "해외로 확대한다고 설명했다. 가격은 전작과 비슷한 수준으로 책정됐다."
)
MAGOK_C = (
    "서울 마곡동 열수송관 파열로 강서구 일대 약 9천 세대에 난방과 온수 공급이 끊겼다. 서울에너지공사는 "
    "긴급 복구팀을 투입해 복구 작업을 벌이고 있으며 불편을 겪는 주민들에게 임시 난방기를 지원한다고 밝혔다. "
    "온수관 파열 원인은 조사 중이다."
)
OTHER = (
    "기상청은 7일 중부지방을 중심으로 올가을 첫 얼음이 관측됐다고 밝혔다. 설악산은 영하 9도까지 "
    "떨어졌고 서울 인천 대전 곳곳에서도 첫얼음이 얼었다. 내일 아침까지 추위가 이어지겠으며 "
    "한파 대비가 필요하다는 당부가 나왔다. 기온은 주말부터 서서히 오를 전망이다."
)




def settings(**kw) -> Settings:
    kw.setdefault("min_text_chars", 100)  # 테스트용 짧은 본문에 맞춤
    s = Settings(fetch_workers=1, **kw)
    s.validate()
    return s


def item(title, link, source="", related=(), include_self=False, desc=None):
    """RSS 항목 하나. related=[(제목, 링크, 언론사)]. include_self면 description에 대표 기사도 다시 넣는다."""
    return dict(title=title, link=link, source=source, related=list(related), include_self=include_self, desc=desc)


def rss(items: list[dict]) -> str:
    body = ""
    for i, it in enumerate(items):
        s = f'<source url="https://x.example">{it["source"]}</source>' if it["source"] else ""
        lis = []
        if it["include_self"]:
            lis.append((it["title"], it["link"], it["source"]))
        lis += it["related"]
        if it["desc"] is not None:
            d = f"<description><![CDATA[{it['desc']}]]></description>"
        elif lis:
            html = "".join(
                f'<li><a href="{u}" target="_blank">{t}</a>&nbsp;&nbsp;<font color="#6f6f6f">{src}</font></li>'
                for t, u, src in lis
            )
            d = f"<description><![CDATA[<ol>{html}</ol>]]></description>"
        else:
            d = ""
        body += (
            f"<item><title>{it['title']}</title><link>{it['link']}</link>"
            f"<pubDate>Sat, 07 Nov 2026 0{i % 9}:00:00 GMT</pubDate>{d}{s}</item>"
        )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'


def run(items, bodies, reals=None, **kw):
    """bodies: {링크: 본문 또는 None(추출 실패)}, reals: {링크: 실제 원문 주소}"""
    s = settings(**kw)
    feed = parse_feed(feedparser.parse(rss(items)), s)
    reals = reals or {}

    def fetcher(a: Article):
        text = bodies.get(a.url)
        return (text, reals.get(a.url, a.url), "") if text else ("", "", "테스트: 추출 실패")

    return build_top_events(feed, s, fetcher=fetcher)


# 1. 서로 다른 RSS 항목의 대표 기사들이 같은 사건 -> 한 그룹
def test_representatives_same_event_merged():
    events, _ = run(
        [item("마곡동 온수관 파열…9천 세대 난방 중단 - 연합뉴스", "u1", "연합뉴스"),
         item("추위 속 수천 세대 떨었다 지역난방 사고 - KBS", "u2", "KBS")],
        {"u1": MAGOK_A, "u2": MAGOK_B},
    )
    assert len(events) == 1
    e = events[0]
    assert e.rss_item_ranks == [1, 2] and e.best_rank == 1 and e.source_count == 2


# 2. 제목은 다르지만 본문이 같은 사건
def test_different_titles_same_event():
    t1, t2 = "마곡동 온수관 파열…9천 세대 난방 중단", "추위 속 수천 세대 떨었다 지역난방 사고"
    assert title_similarity(t1, t2) < 0.3  # 제목만 보면 달라 보임
    events, log = run([item(t1 + " - A", "u1", "A"), item(t2 + " - B", "u2", "B")], {"u1": MAGOK_A, "u2": MAGOK_B})
    assert len(events) == 1 and events[0].comparison_basis == "body"
    assert any(r["method"] == "body" and r["same_event"] for r in log)


# 3. 제목이 비슷하고 같은 기업이지만 본문상 다른 사건 -> 별도 그룹
def test_same_company_different_event_separate():
    events, log = run(
        [item("삼성전자, 3분기 발표 - A", "u1", "A"), item("삼성전자, 신제품 발표 - B", "u2", "B")],
        {"u1": SAMSUNG_EARNINGS, "u2": SAMSUNG_PHONE},
    )
    assert len(events) == 2
    assert all(r["method"] == "body" and not r["same_event"] for r in log)


# 4. description에 같은 사건 관련 기사 -> 관련 기사와 언론사 수 반영
def test_related_same_event_counted():
    events, _ = run(
        [item("a - 연합뉴스", "u1", "연합뉴스", related=[("지역난방 사고", "r1", "KBS"), ("마곡 온수관", "r2", "MBC")])],
        {"u1": MAGOK_A, "r1": MAGOK_B, "r2": MAGOK_C},
    )
    e = events[0]
    assert len(events) == 1 and len(e.articles) == 3
    assert [a.kind for a in e.articles] == ["representative", "related", "related"]
    assert e.source_count == 3 and e.source_score == 0.5 and e.unconfirmed_related == []
    assert e.rss_item_ranks == [1]  # 관련 기사에는 별도 RSS 순위가 없다
    assert all(a.rank == 0 for a in e.articles if a.kind == "related")


# 5. 관련은 있지만 다른 사건 -> 언론사 수에 포함하지 않음
def test_related_different_event_excluded():
    events, _ = run(
        [item("a - 연합뉴스", "u1", "연합뉴스", related=[("지역난방 사고", "r1", "KBS"), ("삼성 신제품", "r2", "SBS")])],
        {"u1": MAGOK_A, "r1": MAGOK_B, "r2": SAMSUNG_PHONE},
    )
    e = events[0]
    assert e.source_count == 2 and len(e.articles) == 2
    assert [u["source"] for u in e.unconfirmed_related] == ["SBS"]
    assert any("제외" in n for n in e.notes)


# 6. description에 대표 기사가 다시 들어 있고, 여러 항목에 같은 기사가 반복 -> 중복 집계 없음
def test_no_double_counting():
    g = "https://news.google.com/rss/articles/"
    events, _ = run(
        [item("a - 연합뉴스", g + "A?oc=5", "연합뉴스", include_self=True,
              related=[("지역난방 사고", g + "R1?oc=5", "KBS"), ("두 번째 항목 대표", g + "B?oc=5", "MBC")]),
         item("b - MBC", g + "B?oc=5", "MBC", include_self=True,
              related=[("지역난방 사고", g + "R1?oc=5&x=1", "KBS")])],
        {g + "A?oc=5": MAGOK_A, g + "B?oc=5": MAGOK_C, g + "R1?oc=5": MAGOK_B, g + "R1?oc=5&x=1": MAGOK_B},
    )
    assert len(events) == 1
    e = events[0]
    assert len(e.articles) == 3  # A, B(대표), R1 한 번씩
    assert [a.kind for a in e.articles].count("related") == 1
    assert e.source_count == 3
    assert e.rss_item_ranks == [1, 2]


def test_duplicate_detected_after_resolving_real_url():
    # 구글 링크는 다르지만 원문 주소가 같은 관련 기사는 한 번만 센다
    events, _ = run(
        [item("a - 연합뉴스", "g1", "연합뉴스", related=[("같은 기사", "g2", "연합뉴스")])],
        {"g1": MAGOK_A, "g2": MAGOK_A},
        reals={"g1": "https://real.example/a", "g2": "https://real.example/a"},
    )
    assert len(events[0].articles) == 1


# 7. 같은 언론사의 여러 기사 -> 1곳
def test_same_source_counted_once():
    events, _ = run(
        [item("a - KBS", "u1", "KBS", related=[("b", "r1", " kbs "), ("c", "r2", "연합뉴스")])],
        {"u1": MAGOK_A, "r1": MAGOK_B, "r2": MAGOK_C},
    )
    assert len(events[0].articles) == 3 and events[0].source_count == 2


# 8. description / 관련 기사 목록이 없음
@pytest.mark.parametrize("desc", [None, "그냥 글자만 있는 설명", "<ol><li>링크 없는 항목</li></ol>", "<ol><li><a>깨진"])
def test_missing_or_odd_description(desc):
    events, _ = run([item("a - 연합뉴스", "u1", "연합뉴스", desc=desc)], {"u1": MAGOK_A})
    assert len(events) == 1 and len(events[0].articles) == 1 and events[0].source_count == 1


# 9. 본문 추출 실패 -> 제목으로 보조 판단, 실패 상태 표시, 불확실하면 제외
def test_extraction_failure_title_fallback():
    rep = "서울 마곡동 온수관 파열 9천 세대 난방 중단"
    events, log = run(
        [item(rep + " - 연합뉴스", "u1", "연합뉴스",
              related=[("서울 마곡동 온수관 파열 9천 세대 난방·온수 중단", "r1", "KBS"), ("오늘의 날씨 한파 소식", "r2", "MBC")])],
        {"u1": None, "r1": None, "r2": None},
    )
    e = events[0]
    assert [a.source for a in e.articles] == ["연합뉴스", "KBS"]
    assert e.comparison_basis == "title_fallback"
    assert e.text_statuses == {"failed": 2}
    assert any("제목" in n for n in e.notes)
    assert [u["source"] for u in e.unconfirmed_related] == ["MBC"]  # 불확실 -> 제외
    assert any(r["method"] == "title_fallback" and r["stage"] == "related" for r in log)


def test_too_short_body_is_not_compared_by_body():
    events, _ = run([item("a - A", "u1", "A", related=[("b", "r1", "B")])], {"u1": MAGOK_A, "r1": "너무 짧음"})
    assert events[0].articles[0].text_status == "ok"
    assert len(events[0].articles) == 1  # 짧은 본문 + 다른 제목 -> 확인 못 함 -> 제외
    assert events[0].unconfirmed_related[0]["text_status"] == "too_short"


# 10. 언론사 정보 없음
def test_missing_source():
    events, _ = run([item("a", "u1", "", related=[("b", "r1", "")])], {"u1": MAGOK_A, "r1": MAGOK_B})
    e = events[0]
    assert len(e.articles) == 2
    assert e.source_count == 0 and e.source_score == 0 and e.source_status == "unknown"
    assert any("언론사" in n for n in e.notes)


def test_partial_source_info():
    events, _ = run([item("a", "u1", "KBS", related=[("b", "r1", "")])], {"u1": MAGOK_A, "r1": MAGOK_B})
    assert events[0].source_count == 1 and events[0].source_status == "partial"


# 11. RSS 항목 없음 / 1개 / 5개 미만 / 5개 초과
def test_empty_feed():
    events, log = run([], {})
    assert events == [] and log == []


def test_single_item():
    events, _ = run([item("a - A", "u1", "A")], {"u1": MAGOK_A})
    assert len(events) == 1 and events[0].rank_score == 1.0 and events[0].source_score == 0.0
    assert events[0].total_score == pytest.approx(0.85)


def test_fewer_and_more_than_five():
    events, _ = run([item("a", "u1", "A"), item("b", "u2", "B")], {"u1": MAGOK_A, "u2": SAMSUNG_PHONE})
    assert len(events) == 2
    topics = [MAGOK_A, SAMSUNG_PHONE, SAMSUNG_EARNINGS, OTHER,
              "국회는 예산안 심사를 시작했다 여야는 증액 규모를 놓고 이견을 보였다 " * 5,
              "프로야구 한국시리즈 4차전에서 삼성이 승리하며 시리즈 균형을 맞췄다 선발 투수가 호투했다 " * 5,
              "환율이 급등해 코스피가 하락했다 외국인 투자자의 매도세가 이어졌다 " * 5]
    items = [item(f"제목{i}", f"u{i}", f"언론{i}") for i in range(7)]
    events, _ = run(items, {f"u{i}": t for i, t in enumerate(topics)})
    assert len(events) == 5 and [e.rank for e in events] == [1, 2, 3, 4, 5]


# 12. 동점 -> RSS 노출 순서가 앞선 그룹 우선
def test_tie_goes_to_earlier_rss_rank():
    events, _ = run(
        [item("a", "u1", "A"), item("b", "u2", "B")],
        {"u1": SAMSUNG_PHONE, "u2": OTHER},
        rank_weight=0.0, source_weight=1.0,
    )
    assert events[0].total_score == events[1].total_score
    assert [e.best_rank for e in events] == [1, 2]


# 점수 경계값
@pytest.mark.parametrize("n,expected", [(0, 0.0), (1, 0.0), (2, 0.25), (3, 0.5), (5, 1.0), (9, 1.0)])
def test_source_score_boundaries(n, expected):
    assert source_score(n, 5) == expected


@pytest.mark.parametrize("rank,total,expected", [(1, 1, 1.0), (1, 10, 1.0), (10, 10, 0.0), (6, 11, 0.5)])
def test_rank_score_boundaries(rank, total, expected):
    assert rank_score(rank, total) == expected


def test_total_score_formula():
    # 3위/11개 -> 순서 0.8, 언론사 3곳 -> 0.5 : 0.8*0.85 + 0.5*0.15 = 0.755
    assert rank_score(3, 11) * 0.85 + source_score(3, 5) * 0.15 == pytest.approx(0.755)


def test_config_validation():
    with pytest.raises(ValueError):
        Settings(source_cap=1).validate()
    with pytest.raises(ValueError):
        Settings(rank_weight=-1).validate()


# 수집/파싱 보조
def test_rss_robustness():
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        "<item><title>제목없는링크</title></item>"
        "<item><link>u1</link></item>"
        "<item><title>정상 - 연합</title><link>u2</link></item>"
        "<item><title>중복</title><link>u2</link></item></channel></rss>"
    )
    feed = parse_feed(feedparser.parse(xml))
    assert feed.total_items == 4 and len(feed.reps) == 1 and feed.related == []
    a = feed.reps[0]
    assert a.rank == 3 and a.published == "" and a.source == "" and a.description == ""


def test_norm_url_and_parse_related():
    assert norm_url("https://news.google.com/rss/articles/ABC?oc=5") == norm_url("https://news.google.com/rss/articles/ABC")
    assert norm_url("https://a.com/x/?utm_source=z#top") == norm_url("https://A.com/x")
    html = '<ol><li><a href="u1">제목&nbsp;1</a>&nbsp;&nbsp;<font>KBS</font></li><li><a href="u2">제목2</a></li></ol>'
    assert parse_related(html) == [
        {"url": "u1", "title": "제목 1", "source": "KBS"},
        {"url": "u2", "title": "제목2", "source": ""},
    ]
    assert parse_related("") == [] and parse_related("텍스트") == []
