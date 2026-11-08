import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

from newscard.config import Settings, load_settings
from newscard.models import Article

log = logging.getLogger(__name__)


@dataclass
class FeedData:
    reps: list[Article] = field(default_factory=list)  # RSS 항목의 대표 기사
    related: list[Article] = field(default_factory=list)  # description 속 관련 기사
    total_items: int = 0  # RSS 전체 항목 수


def clean_title(title: str) -> str:
    """구글 제목 끝의 ' - 언론사' 부분을 뗀다."""
    return title.rsplit(" - ", 1)[0].strip() if " - " in title else title.strip()


def norm_url(url: str) -> str:
    """같은 기사를 같은 키로 만들기 위한 주소 정규화. 추적용 파라미터와 #조각을 뗀다.

    구글 뉴스 링크(news.google.com/rss/articles/ID?oc=5)는 ID 부분만 남는다.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = parts.netloc.lower()
    if host == "news.google.com":
        return f"news.google.com{parts.path}"
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "oc", "ref"))]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def norm_text(text: str) -> str:
    return "".join(text.split()).casefold()


class _RelatedParser(HTMLParser):
    """description HTML에서 <li><a href>제목</a> <font>언론사</font></li> 목록을 뽑는다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._cur: dict | None = None
        self._in_a = False
        self._in_font = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li":
            self._flush()
            self._cur = {"url": "", "title": "", "source": ""}
        elif tag == "a":
            if self._cur is None:
                self._cur = {"url": "", "title": "", "source": ""}
            if not self._cur["url"]:
                self._cur["url"] = a.get("href") or ""
            self._in_a = True
        elif tag == "font":
            self._in_font = True

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_a = False
        elif tag == "font":
            self._in_font = False
        elif tag == "li":
            self._flush()

    def handle_data(self, data):
        if self._cur is None:
            return
        if self._in_a:
            self._cur["title"] += data
        elif self._in_font:
            self._cur["source"] += data

    def _flush(self):
        if self._cur and self._cur["url"]:
            self.items.append({k: " ".join(v.replace("\xa0", " ").split()) for k, v in self._cur.items()})
        self._cur = None

    def close(self):
        super().close()
        self._flush()


def parse_related(description: str) -> list[dict]:
    """description에서 관련 기사 목록을 뽑는다. 없거나 형식이 달라도 빈 목록."""
    if not description or "<" not in description:
        return []
    try:
        p = _RelatedParser()
        p.feed(description)
        p.close()
        return p.items
    except Exception as e:  # HTML이 깨져 있어도 전체 수집은 멈추지 않는다
        log.warning("description 파싱 실패: %s", type(e).__name__)
        return []


def parse_feed(parsed, settings: Settings | None = None, now: datetime | None = None) -> FeedData:
    """feedparser 결과를 대표 기사 + 관련 기사로 바꾼다.

    - 대표 기사의 rank는 RSS 노출 순서(첫 항목이 1).
    - 관련 기사에는 별도 순위가 없고, 연결된 RSS 항목의 순서를 parents에 기록한다.
    - description에 대표 기사가 다시 들어 있거나 같은 기사가 반복되면 한 번만 센다.
    """
    settings = settings or Settings()
    collected_at = (now or datetime.now(timezone.utc)).isoformat()
    entries = list(parsed.entries)
    data = FeedData(total_items=len(entries))

    # 1) 대표 기사
    rep_keys: dict[str, Article] = {}
    for position, entry in enumerate(entries, start=1):
        link, title = entry.get("link", ""), entry.get("title", "")
        if not link or not title:
            log.warning("RSS %d번째 항목 건너뜀: 제목 또는 링크 없음", position)
            continue
        key = norm_url(link)
        if key in rep_keys:
            log.info("RSS %d번째 항목 건너뜀: 같은 링크 중복", position)
            continue
        art = Article(
            url=link,
            title=clean_title(title),
            source=((entry.get("source") or {}).get("title", "") or "").strip(),
            published=entry.get("published", "") or "",
            collected_at=collected_at,
            rank=position,
            description=entry.get("summary", "") or "",
        )
        rep_keys[key] = art
        data.reps.append(art)

    # 2) 관련 기사
    rep_title_source = {(norm_text(a.title), norm_text(a.source)) for a in data.reps}
    related: dict[str, Article] = {}
    for rep in data.reps:
        items = parse_related(rep.description)
        used = 0
        for it in items:
            key = norm_url(it["url"])
            title = clean_title(it["title"])
            if key in rep_keys or (norm_text(title), norm_text(it["source"])) in rep_title_source:
                continue  # 대표 기사가 description에 다시 들어 있거나, 다른 항목의 대표 기사
            if key in related:
                if rep.rank not in related[key].parents:
                    related[key].parents.append(rep.rank)  # 여러 RSS 항목에 반복 등장: 한 번만 센다
                continue
            if not title:
                continue
            if used >= settings.max_related_per_item:
                break
            used += 1
            related[key] = Article(
                url=it["url"], title=title, source=it["source"], kind="related",
                parents=[rep.rank], collected_at=collected_at,
            )
    data.related = list(related.values())
    return data


def collect(settings: Settings | None = None) -> FeedData:
    """구글 뉴스 한국 종합 RSS 하나를 읽는다."""
    settings = settings or load_settings()
    parsed = feedparser.parse(settings.rss_url)
    if not parsed.entries:
        log.warning("RSS에서 항목을 받지 못했습니다: %s", getattr(parsed, "bozo_exception", ""))
    return parse_feed(parsed, settings)
