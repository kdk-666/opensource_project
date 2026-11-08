"""본문 내용을 기준으로 같은 사건을 다룬 기사끼리 묶는다.

두 단계로 일한다.
 A. RSS 항목의 대표 기사들끼리 같은 사건이면 하나의 그룹으로 묶는다. (Top 5 후보는 여기서만 나온다)
 B. description 속 관련 기사는 연결된 대표 기사와 같은 사건으로 확인될 때만 그 그룹에 붙인다.
    확인하지 못하면 그룹에 넣지 않고 '제외된 관련 기사'로 기록한다. (언론사 수에 포함하지 않음)

방식 (무거운 모델 없이 표준 라이브러리만 사용):
1. 본문을 단어로 쪼개고 조사를 떼서 핵심 단어(고유명사, 숫자, 행위 단어)를 얻는다.
2. 이번에 수집한 기사들 안에서 TF-IDF 가중치를 계산한다.
   여러 기사에 흔한 단어는 덜, 특정 기사에만 나오는 단어는 더 반영된다.
3. 두 기사의 본문 코사인 유사도가 기준 이상이면 같은 사건 후보로 본다.
4. 그룹에 넣을 때는 그룹의 모든 기사와 기준을 통과해야 한다. (애매하면 합치지 않는다)
5. 본문 비교가 불가능한 기사는 제목 유사도(더 엄격한 기준)로만 보조 판단하고, 그렇게 판단했음을 기록한다.
6. 발행 시각 차이가 크면 후보에서 제외한다. (시각 정보가 없으면 이 조건은 쓰지 않는다)
"""
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime

from newscard.config import Settings
from newscard.models import Article

log = logging.getLogger(__name__)

_JOSA = sorted(
    ["에서는", "에서의", "으로는", "에서", "으로", "에게", "까지", "부터", "라고", "이라", "에는",
     "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "로", "만"],
    key=len,
    reverse=True,
)
_STOP = {
    "기자", "연합뉴스", "사진", "제공", "오늘", "이번", "관련", "대한", "위해", "통해", "따르면",
    "밝혔다", "말했다", "설명했다", "있다", "있는", "했다", "한다", "것으로", "이후", "지난",
    "무단전재", "재배포", "금지", "뉴스", "기사",
}
_WORD = re.compile(r"[가-힣]+|[A-Za-z]+|\d+")


def tokens(text: str) -> list[str]:
    """본문을 핵심 단어 목록으로 만든다. 한 글자 한글 단어와 불용어는 버린다."""
    out = []
    for w in _WORD.findall(text.lower()):
        if "가" <= w[0] <= "힣":
            for suf in _JOSA:
                if w.endswith(suf) and len(w) - len(suf) >= 2:
                    w = w[: -len(suf)]
                    break
            if len(w) < 2:
                continue
        elif w.isdigit() and len(w) < 2:
            continue
        if w in _STOP:
            continue
        out.append(w)
        # 어미가 달라도 이어지도록 앞 2글자도 단어로 센다. '파열되면서'/'파열돼' -> '파열'
        if "가" <= w[0] <= "힣" and len(w) >= 3:
            out.append(w[:2])
    return out


def _bigrams(text: str) -> set[str]:
    s = re.sub(r"\W", "", text.lower())
    return {s[i : i + 2] for i in range(len(s) - 1)}


def title_similarity(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def parse_dt(text: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return dt


def is_comparable(a: Article, settings: Settings) -> bool:
    return a.text_status == "ok" and len(a.text) >= settings.min_text_chars


def _vectors(articles: list[Article], settings: Settings) -> dict[str, dict[str, float]]:
    """본문이 있는 기사들의 TF-IDF 벡터 (길이 1로 정규화)."""
    docs = {a.url: Counter(tokens(a.text)) for a in articles if is_comparable(a, settings)}
    n = len(docs)
    df: Counter = Counter()
    for c in docs.values():
        df.update(c.keys())
    vecs = {}
    for url, c in docs.items():
        v = {w: (1 + math.log(tf)) * (math.log((n + 1) / (df[w] + 1)) + 1) for w, tf in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs[url] = {w: x / norm for w, x in v.items()}
    return vecs


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


@dataclass
class Group:
    members: list[Article] = field(default_factory=list)  # 대표 기사들 + 확인된 관련 기사
    methods: set[str] = field(default_factory=set)  # 이 그룹을 만들 때 쓴 비교 방식
    unconfirmed: list[dict] = field(default_factory=list)  # 같은 사건으로 확인하지 못한 관련 기사


def compare(a: Article, b: Article, vecs: dict, settings: Settings) -> dict:
    """두 기사가 같은 사건인지 판단하고 근거를 돌려준다."""
    rec = {"a": a.title, "b": b.title, "method": "", "similarity": 0.0, "same_event": False, "reason": ""}

    da, db = parse_dt(a.published), parse_dt(b.published)
    if da and db:
        gap = abs((da - db).total_seconds()) / 3600
        if gap > settings.max_gap_hours:
            rec.update(method="time_gap", reason=f"발행 시각 차이 {gap:.0f}시간 > {settings.max_gap_hours}시간")
            return rec

    if a.url in vecs and b.url in vecs:
        sim = _cosine(vecs[a.url], vecs[b.url])
        same = sim >= settings.merge_threshold
        rec.update(
            method="body",
            similarity=round(sim, 3),
            same_event=same,
            reason=f"본문 유사도 {sim:.3f} {'>=' if same else '<'} 기준 {settings.merge_threshold}",
        )
        return rec

    sim = title_similarity(a.title, b.title)
    same = sim >= settings.title_fallback_threshold
    rec.update(
        method="title_fallback",
        similarity=round(sim, 3),
        same_event=same,
        reason=(
            f"본문 비교 불가({a.text_status}/{b.text_status}) → 제목 유사도 {sim:.3f} "
            f"{'>=' if same else '<'} 기준 {settings.title_fallback_threshold}"
        ),
    )
    return rec


def cluster(
    reps: list[Article], related: list[Article], settings: Settings
) -> tuple[list[Group], list[dict]]:
    """대표 기사들을 같은 사건끼리 묶고, 관련 기사를 확인되는 만큼만 붙인다."""
    vecs = _vectors(reps + related, settings)
    groups: list[Group] = []
    decisions: list[dict] = []

    # A. 대표 기사끼리
    for art in sorted(reps, key=lambda x: x.rank):
        best: Group | None = None
        best_score = -1.0
        best_recs: list[dict] = []
        for g in groups:
            recs = []
            ok = True
            for m in g.members:  # 그룹의 모든 기사와 통과해야 함 (보수적)
                r = compare(art, m, vecs, settings)
                r["stage"] = "representatives"
                recs.append(r)
                decisions.append(r)
                if not r["same_event"]:
                    ok = False
                    break
            if ok:
                score = sum(r["similarity"] for r in recs) / len(recs)
                if score > best_score:
                    best, best_score, best_recs = g, score, recs
        if best is None:
            groups.append(Group(members=[art]))
            log.info("새 사건 그룹: [%d위] %s", art.rank, art.title)
        else:
            best.members.append(art)
            best.methods.update(r["method"] for r in best_recs)
            log.info(
                "대표 기사끼리 합침: [%d위] %s (%s)", art.rank, art.title,
                ", ".join(sorted({r["method"] for r in best_recs})),
            )

    # B. 관련 기사를 연결된 대표 기사와 비교
    by_rank = {m.rank: (g, m) for g in groups for m in g.members}
    for rel in sorted(related, key=lambda x: min(x.parents) if x.parents else 10**9):
        placed = False
        last_rec: dict | None = None
        for p in sorted(rel.parents):
            if p not in by_rank:
                continue
            g, parent = by_rank[p]
            rec = compare(rel, parent, vecs, settings)
            rec.update(stage="related", related_to_rss_rank=p)
            decisions.append(rec)
            last_rec = rec
            if rec["same_event"]:
                g.members.append(rel)
                g.methods.add(rec["method"])
                placed = True
                log.info("관련 기사 연결: %s (%s) -> [%d위] %s", rel.title[:30], rec["method"], p, parent.title[:30])
                break
        if not placed:
            for p in rel.parents:
                if p in by_rank:
                    by_rank[p][0].unconfirmed.append(
                        {
                            "title": rel.title, "source": rel.source, "url": rel.url,
                            "text_status": rel.text_status,
                            "reason": (last_rec or {}).get("reason", "연결된 대표 기사를 찾지 못함"),
                        }
                    )
    return groups, decisions
