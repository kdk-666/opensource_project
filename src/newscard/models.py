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


class EventSummary(BaseModel):
    """Top 사건 하나의 요약. 대본/카드 단계가 이 파일을 읽는다."""

    rank: int  # TopEvent.rank
    event_title: str  # 대표 기사 제목 (원본)
    event_url: str
    headline: str = ""  # 카드 제목 (LLM 생성)
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)  # 요약에 실제로 쓴 기사 [{source, title, url}] (대표 기사)
    also_reported_by: list[str] = Field(default_factory=list)  # 같은 사건으로 확인된 다른 언론사 (요약에는 미사용)
    basis: str = "body"  # body(대표 기사 본문) | body_fallback(대표 기사 본문이 없어 같은 사건의 다른 기사 본문 사용) | title_only(제목만, 제한적)
    input_chars: int = 0  # 모델에 준 본문 글자 수
    truncated: bool = False  # 본문이 너무 길어 일부만 줬는지
    model: str = ""
    status: str = "ok"  # ok | failed
    error: str = ""
    elapsed_sec: float = 0.0  # 이 사건 요약에 걸린 전체 시간 (재시도 포함)
    attempts: int = 0  # 모델 호출 횟수
    prompt_tokens: int | None = None  # Ollama가 알려준 입력 토큰 수 (성공한 호출 기준)
    output_tokens: int | None = None  # Ollama가 알려준 출력 토큰 수
    load_sec: float | None = None  # 모델을 메모리에 올리는 데 걸린 시간 (처음 한 번만 큼)


class ScriptCard(BaseModel):
    """카드뉴스 한 장 + 그 장의 나레이션. 카드 이미지(Pillow)와 음성(Piper) 단계가 이 파일을 읽는다."""

    index: int  # 영상 안에서의 순서 (0부터)
    kind: str  # intro | news | outro
    rank: int = 0  # news일 때 Top 순위
    title: str  # 카드 제목
    bullets: list[str] = Field(default_factory=list)  # 카드에 보여줄 짧은 줄
    narration: str  # 음성으로 읽을 문장
    est_sec: float = 0.0  # 나레이션 예상 길이(초). 실제 음성 길이가 아닌 글자 수 기반 추정
    source_line: str = ""  # 카드 하단 출처 표기
    basis: str = ""  # 요약 근거 (body | body_fallback | title_only), news일 때
    generated_by: str = "template"  # llm | fallback(모델 실패 시 요약문으로 대체) | template
    model: str = ""
    elapsed_sec: float = 0.0
    attempts: int = 0
    error: str = ""


class VerifyResult(BaseModel):
    """요약 검토 결과. summaries.json은 건드리지 않고 data/verification.json에 따로 저장한다."""

    rank: int
    headline: str = ""
    status: str  # pass(문제 없음) | warn(근거 확인 불충분, 검토 권장) | flag(근거 없는 내용 의심, 검토 필요) | skipped
    rule_issues: list[str] = Field(default_factory=list)  # 코드 검사: 본문에 없는 숫자/인용문/영문 이름
    claims: list[dict] = Field(default_factory=list)  # LLM 검토: [{text, supported, evidence, verdict}]
    llm_status: str = "not_run"  # ok | failed | skipped | not_run
    basis: str = ""  # 요약 근거 (body | body_fallback | title_only)
    model: str = ""
    elapsed_sec: float = 0.0
    attempts: int = 0
    notes: list[str] = Field(default_factory=list)


class ScriptCheck(BaseModel):
    """대본 카드 한 장의 코드 검사 결과. data/script_check.json에 저장한다."""

    index: int
    kind: str  # intro | news | outro
    rank: int = 0
    status: str  # pass | warn | flag
    issues: list[str] = Field(default_factory=list)  # 고쳐야 할 문제 (flag)
    warnings: list[str] = Field(default_factory=list)  # 확인하면 좋은 점 (warn)
    est_sec: float = 0.0
