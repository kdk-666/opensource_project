import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Callable

import trafilatura
from googlenewsdecoder import gnewsdecoder
from trafilatura.settings import use_config

from newscard.config import Settings
from newscard.models import Article

log = logging.getLogger(__name__)

# fetcher(article) -> (본문, 실제 주소, 오류 사유). 테스트에서는 이 자리에 가짜 함수를 넣는다.
Fetcher = Callable[[Article], tuple[str, str, str]]


def resolve_url(url: str) -> tuple[str, str]:
    """구글 뉴스 중계 주소를 실제 기사 주소로 바꾼다. (주소, 오류 사유)"""
    try:
        result = gnewsdecoder(url)
    except Exception as e:  # 네트워크 오류 등
        return "", f"주소 풀기 오류: {type(e).__name__}"
    if (result.get("success") or result.get("status")) and result.get("decoded_url"):
        return result["decoded_url"], ""
    return "", f"주소 풀기 실패: {result.get('message', '사유 없음')}"


def make_fetcher(settings: Settings) -> Fetcher:
    """실제 인터넷에서 본문을 가져오는 fetcher. 접근이 막히면 우회하지 않고 사유만 남긴다."""
    config = use_config()
    config.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(settings.fetch_timeout))

    def fetch(article: Article) -> tuple[str, str, str]:
        real, err = resolve_url(article.url)
        if not real:
            return "", "", err
        try:
            html = trafilatura.fetch_url(real, config=config)
        except Exception as e:
            return "", real, f"내려받기 오류: {type(e).__name__}"
        if not html:
            return "", real, "내려받기 실패(응답 없음/접근 거부/시간 초과, 정확한 사유는 알 수 없음)"
        try:
            text = trafilatura.extract(html) or ""
        except Exception as e:
            return "", real, f"본문 추출 오류: {type(e).__name__}"
        if not text:
            return "", real, "본문을 찾지 못함"
        return text, real, ""

    return fetch


def _apply(article: Article, text: str, real: str, err: str, min_chars: int) -> None:
    article.real_url = real
    article.text = text
    if not text:
        article.text_status, article.text_error = "failed", err or "본문 없음"
    elif len(text) < min_chars:
        article.text_status = "too_short"
        article.text_error = f"본문이 {len(text)}자로 짧아 비교 불가 (최소 {min_chars}자)"
    else:
        article.text_status, article.text_error = "ok", ""


def fill_texts(
    articles: list[Article],
    settings: Settings,
    fetcher: Fetcher | None = None,
    cache_path: Path | None = None,
) -> None:
    """모든 기사에 본문과 추출 상태를 채운다. 실패해도 멈추지 않고 다음 기사로 넘어간다."""
    fetcher = fetcher or make_fetcher(settings)

    cache: dict[str, dict] = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("캐시 파일이 깨져 있어 무시합니다: %s", cache_path)

    todo = []
    for a in articles:
        hit = cache.get(a.url)
        if isinstance(hit, dict) and hit.get("text"):  # 옛 형식 캐시는 무시
            _apply(a, hit["text"], hit.get("real_url", ""), "", settings.min_text_chars)
        else:
            todo.append(a)

    pool = ThreadPoolExecutor(max_workers=settings.fetch_workers)
    try:
        futures = [(a, pool.submit(fetcher, a)) for a in todo]
        for a, fut in futures:
            try:
                text, real, err = fut.result(timeout=settings.fetch_timeout * 4)
            except FutureTimeout:
                text, real, err = "", "", "시간 초과"
            except Exception as e:  # fetcher 안의 예기치 못한 오류도 전체를 멈추지 않는다
                text, real, err = "", "", f"예상치 못한 오류: {type(e).__name__}"
            _apply(a, text, real, err, settings.min_text_chars)
            if a.text_status != "ok":
                log.info("본문 %s: %s (%s)", a.text_status, a.title[:30], a.text_error)
            if a.text_status == "ok" and cache_path:
                cache[a.url] = {"text": a.text, "real_url": a.real_url}  # 성공만 캐시
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
