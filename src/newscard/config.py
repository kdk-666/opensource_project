import os
from dataclasses import dataclass, fields
from pathlib import Path

import yaml


@dataclass
class Settings:
    # 수집
    rss_url: str = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
    top_n: int = 5
    max_related_per_item: int = 8  # RSS 항목 하나당 확인할 관련 기사 수 상한
    # 점수: 총점 = RSS 노출 순서 점수 x rank_weight + 언론사 수 점수 x source_weight
    rank_weight: float = 0.85
    source_weight: float = 0.15
    source_cap: int = 5  # 이 수 이상의 언론사면 언론사 수 점수 1.0
    # 본문 추출
    min_text_chars: int = 200  # 이보다 짧으면 비교 불가
    fetch_timeout: int = 15  # 초. 기사 하나당 내려받기 제한 시간
    fetch_workers: int = 4
    # 같은 사건 판단
    merge_threshold: float = 0.30  # 본문 유사도(0~1)가 이 이상이면 같은 사건 후보
    title_fallback_threshold: float = 0.60  # 본문 비교 불가 시 제목 유사도 기준 (엄격)
    max_gap_hours: int = 72  # 발행 시각이 이보다 멀면 같은 사건으로 보지 않음
    # 저장
    data_dir: str = "data"

    def validate(self) -> None:
        if self.top_n < 1:
            raise ValueError("top_n은 1 이상이어야 합니다.")
        if self.source_cap < 2:
            raise ValueError("source_cap은 2 이상이어야 합니다.")
        if self.rank_weight < 0 or self.source_weight < 0:
            raise ValueError("가중치는 0 이상이어야 합니다.")
        if self.rank_weight + self.source_weight <= 0:
            raise ValueError("가중치의 합은 0보다 커야 합니다.")


def load_settings(path: str | None = None) -> Settings:
    """설정 파일(yaml)이 있으면 읽어서 기본값을 덮어쓴다. 없으면 기본값."""
    path = path or os.environ.get("NEWSCARD_CONFIG", "config/settings.yaml")
    settings = Settings()
    p = Path(path)
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        valid = {f.name for f in fields(Settings)}
        unknown = set(data) - valid
        if unknown:
            raise ValueError(f"알 수 없는 설정: {sorted(unknown)}")
        for k, v in data.items():
            setattr(settings, k, v)
    settings.validate()
    return settings
