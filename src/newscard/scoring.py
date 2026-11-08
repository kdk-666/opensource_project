from newscard.cluster import Group
from newscard.config import Settings
from newscard.models import Article, TopEvent


def rank_score(best_rank: int, total_items: int) -> float:
    """RSS 노출 순서 점수. 1위 = 1.0, 마지막 = 0.0. 항목이 1개뿐이면 1.0."""
    if total_items <= 1:
        return 1.0
    score = 1 - (best_rank - 1) / (total_items - 1)
    return max(0.0, min(1.0, score))


def source_score(n_sources: int, cap: int) -> float:
    """언론사 수 점수. 1곳 이하 = 0.0, cap곳 이상 = 1.0, 사이는 선형."""
    if n_sources <= 1:
        return 0.0
    if n_sources >= cap:
        return 1.0
    return (n_sources - 1) / (cap - 1)


def _norm_source(name: str) -> str:
    return " ".join(name.split()).casefold()


def count_sources(members: list[Article]) -> tuple[int, str]:
    """서로 다른 언론사 수와 상태. 언론사 정보가 없는 기사는 세지 않는다."""
    known = {_norm_source(m.source) for m in members if m.source.strip()}
    missing = sum(1 for m in members if not m.source.strip())
    if not known:
        return 0, "unknown"
    return len(known), "partial" if missing else "ok"


def _basis(g: Group) -> str:
    if len(g.members) == 1:
        return "single"
    if g.methods == {"body"}:
        return "body"
    if g.methods == {"title_fallback"}:
        return "title_fallback"
    return "mixed"


def score_groups(groups: list[Group], total_items: int, settings: Settings) -> list[TopEvent]:
    """그룹마다 점수를 매기고 상위 top_n개를 TopEvent로 돌려준다."""
    scored = []
    for g in groups:
        reps_in = sorted((m for m in g.members if m.kind == "representative"), key=lambda m: m.rank)
        rel_in = [m for m in g.members if m.kind != "representative"]
        members = reps_in + rel_in
        rep = reps_in[0]  # RSS 노출 순서가 가장 앞선 대표 기사
        n_src, src_status = count_sources(members)
        rs = rank_score(rep.rank, total_items)
        ss = source_score(n_src, settings.source_cap)
        total = rs * settings.rank_weight + ss * settings.source_weight

        statuses: dict[str, int] = {}
        for m in members:
            statuses[m.text_status] = statuses.get(m.text_status, 0) + 1

        notes = []
        basis = _basis(g)
        if basis in ("title_fallback", "mixed"):
            notes.append("일부 기사는 본문이 아니라 제목/발행 시각으로 같은 사건이라 판단함")
        if any(m.text_status != "ok" for m in members):
            notes.append("본문 추출에 실패했거나 짧아 비교하지 못한 기사가 있음")
        if g.unconfirmed:
            notes.append(f"관련 기사 {len(g.unconfirmed)}건은 같은 사건으로 확인하지 못해 언론사 수에서 제외함")
        if src_status == "unknown":
            notes.append("언론사 정보를 확인하지 못해 언론사 수 점수 0점 처리")
        elif src_status == "partial":
            notes.append("일부 기사의 언론사 정보가 없어 언론사 수에서 제외함")

        scored.append(
            TopEvent(
                rank=0,
                title=rep.title,
                url=rep.url,
                source=rep.source,
                articles=members,
                rss_item_ranks=[m.rank for m in reps_in],
                unconfirmed_related=g.unconfirmed,
                best_rank=rep.rank,
                source_count=n_src,
                source_status=src_status,
                rank_score=round(rs, 4),
                source_score=round(ss, 4),
                total_score=round(total, 4),
                comparison_basis=basis,
                text_statuses=statuses,
                notes=notes,
            )
        )
    # 점수 높은 순, 동점이면 RSS 노출 순서가 앞선 그룹 우선
    scored.sort(key=lambda e: (-e.total_score, e.best_rank))
    top = scored[: settings.top_n]
    for i, e in enumerate(top, start=1):
        e.rank = i
    return top
