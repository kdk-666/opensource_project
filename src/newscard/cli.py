import logging
import sys
import time

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

    t0 = time.perf_counter()
    summaries = summarize_run()
    total = time.perf_counter() - t0
    print("\n요약 결과 (data/summaries.json)\n")
    for s in summaries:
        tok = f", 입력 {s.prompt_tokens}토큰/출력 {s.output_tokens}토큰" if s.prompt_tokens is not None else ""
        print(f"{s.rank}. [{s.elapsed_sec:.1f}초{tok}, 호출 {s.attempts}회, 본문 {s.input_chars}자]")
        if s.status == "ok":
            tag = {
                "body": "",
                "body_fallback": f" (대표 기사 본문이 없어 {s.sources[0]['source'] or '다른'} 기사로 요약)",
                "title_only": " (제목만으로 요약, 제한적)",
            }[s.basis]
            print(f"   {s.headline}{tag}\n   {s.summary}")
            for p in s.key_points:
                print(f"   - {p}")
        else:
            print(f"   [요약 실패] {s.event_title}\n   {s.error}")
    print(_timing_line(summaries, total))


def cmd_script() -> None:
    from newscard.script import run as script_run

    t0 = time.perf_counter()
    cards = script_run()
    total = time.perf_counter() - t0
    if not cards:
        print("\n대본을 만들 요약이 없어요. `newscard summarize` 결과를 확인하세요.")
        return
    print("\n카드뉴스 대본 (data/script.json)\n")
    for c in cards:
        label = {"intro": "인트로", "news": f"{c.rank}위", "outro": "아웃트로"}[c.kind]
        by = {"llm": "", "template": "", "fallback": " (모델 실패 -> 요약문으로 대체)"}[c.generated_by]
        print(f"[{c.index}] {label} | {c.title}{by}")
        for b in c.bullets:
            print(f"    - {b}")
        print(f"    \"{c.narration}\"  (약 {c.est_sec}초 추정, 호출 {c.attempts}회, {c.elapsed_sec:.1f}초)")
        if c.source_line:
            print(f"    {c.source_line}")
    est = sum(c.est_sec for c in cards)
    print(f"\n전체 {total:.1f}초 | 영상 길이 약 {est:.0f}초 추정 (나레이션 글자 수 기준, 실제 음성과 다를 수 있음)")


def cmd_verify() -> None:
    from newscard import verify

    t0 = time.perf_counter()
    if len(sys.argv) > 2 and sys.argv[2] == "script":
        _print_script_check()
        return
    if len(sys.argv) > 2 and sys.argv[2] == "selftest":
        r = verify.run_selftest()
        print(f"\n검토 모델 자체 점검 (사건 {r['사건수']}건, 요약에 가짜 주장 한 문장을 끼워 시험)")
        print(f"  가짜 주장 적발: {r['가짜_적발']}/{r['사건수']}   멀쩡한 요약 오탐: {r['원본_오탐']}/{r['사건수']}")
        print(f"  소요 {time.perf_counter() - t0:.1f}초")
        return
    use_llm = len(sys.argv) > 2 and sys.argv[2] == "llm"  # 기본은 코드 검사만
    results = verify.run(use_llm=use_llm)
    icon = {"pass": "통과", "warn": "주의", "flag": "검토 필요", "skipped": "생략"}
    print("\n요약 검토 결과 (data/verification.json)\n")
    for r in results:
        extra = f"  (LLM {r.llm_status}, {r.elapsed_sec:.1f}초, 호출 {r.attempts}회)" if use_llm else ""
        print(f"{r.rank}. [{icon[r.status]}] {r.headline}{extra}")
        for i in r.rule_issues:
            print(f"   [코드 검사] {i}")
        for c in r.claims:
            if c["verdict"] != "supported":
                tag = "근거 없음" if c["verdict"] == "unsupported" else "근거 문장 확인 안 됨"
                print(f"   [LLM] {tag}: {c['text'][:50]}")
        for n in r.notes:
            print(f"   ※ {n}")
    mode = "코드 검사 + LLM 검토" if use_llm else "코드 검사만"
    print(f"\n{mode} | 전체 {time.perf_counter() - t0:.2f}초")


def cmd_cards() -> None:
    from newscard import cards

    arg = sys.argv[2] if len(sys.argv) > 2 else "both"
    if arg not in ("both", "light", "dark"):
        raise SystemExit("사용법: newscard cards [light|dark|both]  (기본: both)")
    themes = ("light", "dark") if arg == "both" else (arg,)
    t0 = time.perf_counter()
    result = cards.run(themes=themes)
    for theme, manifest in result.items():
        label = {"light": "밝은 모드", "dark": "다크 모드"}[theme]
        print(f"\n[{label}] 카드 이미지 {len(manifest)}장 -> data/cards/{theme}/ ({manifest[0]['width']}x{manifest[0]['height']})")
        for m in manifest:
            warn = f"  ※ 글이 넘쳐 본문 {m['dropped_bullets']}줄을 뺌" if m["dropped_bullets"] else ""
            print(f"  [{m['index']}] {m['image']}  | {m['title']}{warn}")
    print(f"\n전체 {time.perf_counter() - t0:.1f}초 (목록: data/cards.json)")


def cmd_tts() -> None:
    from newscard import tts

    t0 = time.perf_counter()
    items = tts.run()
    print(f"\n나레이션 음성 {sum(1 for i in items if i['status'] == 'ok')}/{len(items)}장 -> data/audio/ (목록: data/audio.json)\n")
    for i in items:
        if i["status"] == "ok":
            diff = i["duration_sec"] - i["est_sec"]
            print(f"[{i['index']}] {i['duration_sec']:.1f}초 (대본 추정 {i['est_sec']}초, 차이 {diff:+.1f}초)  {i['audio']}")
            print(f"     읽는 글: {i['spoken_text']}")
        else:
            print(f"[{i['index']}] 실패: {i['error']}")
        for w in i["warnings"]:
            print(f"     ※ {w}")
    total = sum(i["duration_sec"] for i in items)
    print(f"\n음성 길이 합계 {total:.1f}초 | 걸린 시간 {time.perf_counter() - t0:.1f}초")


def cmd_video() -> None:
    from newscard import video

    arg = sys.argv[2] if len(sys.argv) > 2 else "both"
    if arg not in ("both", "light", "dark"):
        raise SystemExit("사용법: newscard video [light|dark|both]  (기본: both)")
    themes = ("light", "dark") if arg == "both" else (arg,)
    t0 = time.perf_counter()
    results = video.run(themes=themes)
    for theme, r in results.items():
        label = {"light": "밝은 모드", "dark": "다크 모드"}[theme]
        print(f"\n[{label}] {r['path']}")
        print(f"  길이 {r['duration']:.1f}초 | {r['width']}x{r['height']} | {r['size'] / 1e6:.1f}MB | 코덱 {', '.join(r['codecs'])}")
        for c in r["cards"]:
            print(f"    [{c['index']}] {c['kind']:<6} {c['seconds']:.1f}초")
        for n in r["notes"]:
            print(f"  ※ {n}")
        if abs(r["duration"] - r["expected"]) > 0.5:
            print(f"  ※ 예상 길이({r['expected']}초)와 {abs(r['duration'] - r['expected']):.1f}초 차이가 나요")
    print(f"\n전체 {time.perf_counter() - t0:.1f}초")


def cmd_run() -> None:
    import argparse

    from newscard import runner

    ap = argparse.ArgumentParser(prog="newscard run", description="수집부터 영상까지 한 번에 실행합니다.")
    ap.add_argument("--from", dest="start", help=f"이 단계부터 시작 ({', '.join(runner.STEP_NAMES)})")
    ap.add_argument("--until", dest="end", help="이 단계까지만 실행")
    ap.add_argument("--theme", choices=["light", "dark", "both"], default="both", help="카드/영상 모드 (기본: both)")
    ap.add_argument("--strict", action="store_true", help="검토에서 문제가 나오면 멈춤")
    ap.add_argument("--no-preflight", action="store_true", help="시작 전 점검을 건너뜀")
    args = ap.parse_args(sys.argv[2:])
    try:
        names = runner.select_steps(args.start, args.end)
    except ValueError as e:
        raise SystemExit(str(e))
    ctx = runner.Context(themes=("light", "dark") if args.theme == "both" else (args.theme,), strict=args.strict)

    print(f"실행할 단계: {' -> '.join(names)}\n")
    if not args.no_preflight:
        problems = runner.preflight(names)
        if problems:
            print("시작 전 점검에서 문제가 발견돼 실행하지 않았어요:\n")
            for p in problems:
                print(f"  - {p}")
            raise SystemExit(1)
        print("시작 전 점검 통과\n")

    report = runner.run_pipeline(ctx, names)
    path = runner.save_report(report)
    print()
    if report["warnings"]:
        print(f"확인이 필요한 항목 {len(report['warnings'])}건:")
        for w in report["warnings"]:
            print(f"  ※ {w}")
        print()
    if report["ok"]:
        print(f"모든 단계 완료 | 총 {runner.fmt_sec(report['total_sec'])} | 보고서 {path}")
    else:
        nxt = runner.STEP_NAMES[runner.STEP_NAMES.index(report["failed_step"])]
        print(f"'{report['failed_step']}' 단계에서 멈췄어요 | 총 {runner.fmt_sec(report['total_sec'])}")
        print(f"원인을 해결한 뒤 이어서 하려면: newscard run --from {nxt}")
        raise SystemExit(1)


def _print_script_check() -> None:
    from newscard import script_check

    t0 = time.perf_counter()
    rep = script_check.run()
    icon = {"pass": "통과", "warn": "주의", "flag": "수정 필요"}
    label = {"intro": "인트로", "outro": "아웃트로"}
    print("\n대본 코드 검사 (data/script_check.json)\n")
    for c in rep["cards"]:
        name = label.get(c["kind"], f"{c['rank']}위")
        print(f"[{c['index']}] {name} [{icon[c['status']]}] 약 {c['est_sec']}초")
        for i in c["issues"]:
            print(f"    ✗ {i}")
        for w in c["warnings"]:
            print(f"    △ {w}")
    for i in rep["structure_issues"]:
        print(f"\n[구조] {i}")
    counts = {k: sum(1 for c in rep["cards"] if c["status"] == k) for k in icon}
    print(f"\n통과 {counts['pass']} / 주의 {counts['warn']} / 수정 필요 {counts['flag']} | "
          f"영상 길이 약 {rep['total_est_sec']:.0f}초 추정 | {time.perf_counter() - t0:.2f}초")


def _timing_line(summaries, total):  # 맨 아래 소요 시간 요약
    done = [s for s in summaries if s.status == "ok"]
    if not done:
        return f"\n전체 {total:.1f}초 (성공한 요약 없음)"
    avg = sum(s.elapsed_sec for s in done) / len(done)
    slow = max(done, key=lambda s: s.elapsed_sec)
    loads = [s.load_sec for s in done if s.load_sec]
    load = f", 모델 로딩 최대 {max(loads):.1f}초" if loads else ""
    return f"\n전체 {total:.1f}초 | 성공 {len(done)}/{len(summaries)}건 | 평균 {avg:.1f}초 | 가장 느린 것 {slow.rank}위 {slow.elapsed_sec:.1f}초{load}"


COMMANDS = {"top": cmd_top, "summarize": cmd_summarize, "script": cmd_script, "verify": cmd_verify, "cards": cmd_cards, "tts": cmd_tts, "video": cmd_video, "run": cmd_run}


def main() -> None:
    quiet = len(sys.argv) > 1 and sys.argv[1] == "run"  # 한 번에 실행할 때는 단계별 요약만 보이게 한다
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "trafilatura", "courlan", "htmldate"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    name = sys.argv[1] if len(sys.argv) > 1 else "top"  # 인자가 없으면 기존처럼 Top 5 선정
    if name not in COMMANDS:
        raise SystemExit(f"알 수 없는 명령: {name} (사용 가능: {', '.join(COMMANDS)})")
    COMMANDS[name]()


if __name__ == "__main__":
    main()
