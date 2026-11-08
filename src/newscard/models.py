from pydantic import BaseModel, Field


class Article(BaseModel):
    """RSS에서 수집한 기사 한 건."""

    url: str
    title: str
    kind: str = "representative"  # representative(RSS 항목의 대표 기사) | related(description 속 관련 기사)
    parents: list[int] = Field(default_factory=list)  # related일 때: 연결된 RSS 항목들의 노출 순서
    description: str = ""  # representative일 때: RSS description 원문(HTML)
    source: str = ""  # 언론사명. RSS에 없으면 빈 글자
    published: str = ""  # RSS가 준 발행 시각 원문. 없으면 빈 글자
    collected_at: str = ""  # 수집 시각 (ISO 8601, UTC)
    rank: int = 0  # RSS 노출 순서(대표 기사만). 첫 번째 항목이 1. 관련 기사는 0 (별도 순위 없음)
    real_url: str = ""  # 구글 중계 주소를 푼 실제 기사 주소
    text: str = ""  # 추출한 본문
    text_status: str = "pending"  # pending | ok | too_short | failed
    text_error: str = ""  # 실패/부족 사유


class TopEvent(BaseModel):
    """같은 사건을 다룬 기사 묶음 + 점수. 이후 요약/영상 단계가 사용한다."""

    rank: int  # 최종 순위 (1부터)
    title: str  # 대표 기사 제목 (RSS 노출 순서가 가장 앞선 기사)
    url: str  # 대표 기사 링크
    source: str = ""  # 대표 기사 언론사
    articles: list[Article]  # 같은 사건으로 확인된 기사 전체: 대표 기사들 + 관련 기사 (본문 text 포함)
    rss_item_ranks: list[int] = Field(default_factory=list)  # 이 사건에 속한 RSS 항목들의 노출 순서
    unconfirmed_related: list[dict] = Field(default_factory=list)  # 관련 기사 목록에 있었지만 같은 사건으로 확인 못해 제외한 기사
    best_rank: int  # 그룹 안의 가장 앞선 RSS 노출 순서
    source_count: int  # 현재 RSS 피드에서 확인된 서로 다른 언론사 수
    source_status: str  # ok | partial(일부 언론사 정보 없음) | unknown(모두 없음)
    rank_score: float  # RSS 노출 순서 점수 (0~1)
    source_score: float  # 언론사 수 점수 (0~1)
    total_score: float  # 최종 점수
    comparison_basis: str  # single | body | title_fallback | mixed
    text_statuses: dict[str, int] = Field(default_factory=dict)  # 본문 추출 상태별 기사 수
    notes: list[str] = Field(default_factory=list)  # 사람이 읽는 주의사항


# 호환용: 예전 코드의 Summary/Card는 그대로 유지
class Summary(BaseModel):
    """요약과 관심도 점수."""

    article_url: str
    summary: str
    score: int  # 1~10
    reason: str = ""


class Card(BaseModel):
    """카드 한 장."""

    title: str
    body: str
    narration: str
    source: str = ""
