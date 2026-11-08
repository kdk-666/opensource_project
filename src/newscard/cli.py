import logging
import sys

from newscard.config import load_settings
from newscard.pipeline import run

BASIS = {
    "single": "단독 기사",
    "body": "본문 기반",
    "title_fallback": "제목/시각 보조 판단",
    "mixed": "본문+제목 혼합",
}


def cmd_top() -> None:
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


def cmd_summarize() -> None:
    from newscard.summarize import run as summarize_run

    summaries = summarize_run()
    print("\n요약 결과 (data/summaries.json)\n")
    for s in summaries:
        if s.status == "ok":
            tag = {
                "body": "",
                "body_fallback": f" (대표 기사 본문이 없어 {s.sources[0]['source'] or '다른'} 기사로 요약)",
                "title_only": " (제목만으로 요약, 제한적)",
            }[s.basis]
            print(f"{s.rank}. {s.headline}{tag}\n   {s.summary}")
            for p in s.key_points:
                print(f"   - {p}")
        else:
            print(f"{s.rank}. [요약 실패] {s.event_title}\n   {s.error}")


COMMANDS = {"top": cmd_top, "summarize": cmd_summarize}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "trafilatura", "courlan", "htmldate"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    name = sys.argv[1] if len(sys.argv) > 1 else "top"  # 인자가 없으면 기존처럼 Top 5 선정
    if name not in COMMANDS:
        raise SystemExit(f"알 수 없는 명령: {name} (사용 가능: {', '.join(COMMANDS)})")
    COMMANDS[name]()


if __name__ == "__main__":
    main()
