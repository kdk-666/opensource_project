import logging

from newscard.config import load_settings
from newscard.pipeline import run

BASIS = {
    "single": "단독 기사",
    "body": "본문 기반",
    "title_fallback": "제목/시각 보조 판단",
    "mixed": "본문+제목 혼합",
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings()
    events = run(settings)

    print(f"\nTop {len(events)} 사건 (data/top_events.json, 판단 근거는 data/cluster_log.json)\n")
    for e in events:
        print(
            f"{e.rank}. [{e.total_score:.3f}점] {e.title}\n"
            f"   RSS 노출 순서 {e.best_rank}위(점수 {e.rank_score}) | "
            f"피드에서 확인된 언론사 {e.source_count}곳(점수 {e.source_score}) | "
            f"기사 {len(e.articles)}건(RSS 항목 {len(e.rss_item_ranks)}개) | {BASIS[e.comparison_basis]}"
        )
        for n in e.notes:
            print(f"   ※ {n}")


if __name__ == "__main__":
    main()
