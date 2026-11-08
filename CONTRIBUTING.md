# 기여 가이드

## 브랜치와 커밋

- `main`에는 직접 푸시하지 않습니다. 브랜치를 만들어 Pull Request로 합칩니다.
- 브랜치 이름: `feat/기능-이름`, `fix/문제-이름`, `docs/문서-이름`
- 커밋 메시지는 한 줄로 요약합니다: `feat: ...`, `fix: ...`, `docs: ...`, `test: ...`, `chore: ...`
- PR은 서로 한 번씩 읽고 합칩니다. 작게 자주 올리는 편이 리뷰하기 쉽습니다.

## 합치기 전 확인

```powershell
pytest
```

- 새 기능이나 고친 버그에는 테스트를 같이 추가합니다. 테스트는 네트워크, Ollama, GPU 없이 돌아가야 합니다 (가짜 클라이언트를 씁니다).
- 의존성(`pyproject.toml`)을 바꾸면 PR 설명에 적어 주세요. 다른 사람은 `docker compose build`를 다시 해야 합니다.

## 코드 규칙

- 단계 사이는 `data/*.json` 파일로 주고받습니다. 데이터 형식(`src/newscard/models.py`)을 바꿀 때는 뒤 단계에 영향이 없는지 확인하고 PR에 적어 주세요.
- LLM 프롬프트는 `prompts/*.txt`에 두고 코드에 박지 않습니다. 프롬프트를 바꾸면 결과 예시를 PR에 붙여 주세요.
- LLM이 쓴 글은 그대로 믿지 않습니다. 길이, 형식, 숫자, 출처를 코드로 검사하고, 실패해도 전체가 멈추지 않게 합니다.
- 기사 본문, 음성 파일, 영상, `.env`, 개인 설정(`config/settings.yaml` 등)은 커밋하지 않습니다. (`.gitignore` 확인)
- 접근이 막힌 사이트를 우회하는 코드는 넣지 않습니다.

## 환경이 다를 때

- GPU가 있으면 `.env`에 `COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml` 을 추가합니다. (`.env`는 커밋되지 않습니다)
- 사용하는 모델이 다르면 `.env`에 `NEWSCARD_MODEL=...` 을 적습니다.
