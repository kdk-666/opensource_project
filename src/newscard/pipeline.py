import json
import logging
from pathlib import Path

from newscard.cluster import cluster
from newscard.collect import FeedData, collect, norm_url
from newscard.config import Settings
from newscard.extract import Fetcher, fill_texts
from newscard.models import TopEvent
from newscard.scoring import score_groups

log = logging.getLogger(__name__)


def drop_duplicates_by_real_url(feed: FeedData) -> int:
    """원문 주소를 확인한 뒤에도 같은 기사가 남아 있으면 관련 기사 쪽을 지운다. 지운 수를 돌려준다."""
    seen = {norm_url(a.real_url): a for a in feed.reps if a.real_url}
    kept, dropped = [], 0
    for r in feed.related:
        key = norm_url(r.real_url) if r.real_url else ""
        if key and key in seen:
            owner = seen[key]
            if owner.kind == "related":  # 관련 기사끼리 중복이면 부모 항목만 합친다
                owner.parents = sorted(set(owner.parents) | set(r.parents))
            dropped += 1
            continue
        if key:
            seen[key] = r
        kept.append(r)
    feed.related = kept
    return dropped


def build_top_events(
    feed: FeedData,
    settings: Settings,
    fetcher: Fetcher | None = None,
    cache_path: Path | None = None,
) -> tuple[list[TopEvent], list[dict]]:
    """수집 결과 -> 본문 추출 -> 같은 사건 묶기 -> 점수 -> Top N. RSS 항목이 없으면 빈 결과."""
    if not feed.reps:
        return [], []
    fill_texts(feed.reps + feed.related, settings, fetcher=fetcher, cache_path=cache_path)
    dropped = drop_duplicates_by_real_url(feed)
    if dropped:
        log.info("원문 주소가 같은 중복 기사 %d건 제거", dropped)
    groups, decisions = cluster(feed.reps, feed.related, settings)
    return score_groups(groups, feed.total_items, settings), decisions


def run(settings: Settings) -> list[TopEvent]:
    feed = collect(settings)
    log.info("RSS 항목 %d개, 대표 기사 %d건, 관련 기사 %d건", feed.total_items, len(feed.reps), len(feed.related))
    data = Path(settings.data_dir)
    events, decisions = build_top_events(feed, settings, cache_path=data / "cache" / "texts.json")

    data.mkdir(parents=True, exist_ok=True)
    (data / "top_events.json").write_text(
        json.dumps([e.model_dump() for e in events], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (data / "cluster_log.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return events
