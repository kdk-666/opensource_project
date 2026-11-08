"""Ollama에 JSON으로 답을 받는 공통 도우미. 형식이 틀리면 다시 시도한다."""
import json
import logging
import time

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)


def _detail(e: Exception) -> str:
    """어느 항목이 왜 틀렸는지 한 줄로."""
    if isinstance(e, ValidationError):
        parts = [f"{'.'.join(map(str, x['loc'])) or '전체'}: {x['msg']}" for x in e.errors()[:2]]
        return "ValidationError(" + "; ".join(parts) + ")"
    if isinstance(e, json.JSONDecodeError):
        return "JSONDecodeError(JSON이 아님)"
    return type(e).__name__


def ask_json(client, model: str, message: str, schema: type[BaseModel], num_ctx: int = 4096, retries: int = 2):
    """(검증된 결과 또는 None, 시도 횟수, 오류 사유, 걸린 시간)을 돌려준다."""
    started = time.perf_counter()
    error = ""
    attempts = 0
    raw = ""
    for attempt in range(1, retries + 2):
        attempts = attempt
        try:
            resp = client.chat(
                model=model,
                messages=[{"role": "user", "content": message}],
                format="json",
                options={"temperature": 0.3, "num_ctx": num_ctx},
            )
            raw = resp["message"]["content"]
            out = schema.model_validate(json.loads(raw))
            return out, attempts, "", round(time.perf_counter() - started, 2)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as e:
            error = f"응답 형식 오류: {_detail(e)}"
            log.warning("모델 응답 앞부분: %s", (raw or "")[:150].replace("\n", " "))
        except Exception as e:  # 연결 끊김, 모델 없음 등
            error = f"Ollama 호출 오류: {type(e).__name__}: {e}"
        log.warning("시도 %d 실패: %s", attempt, error)
    return None, attempts, error, round(time.perf_counter() - started, 2)
