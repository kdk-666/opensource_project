# newscard

구글 뉴스 한국 종합 RSS에서 주요 뉴스를 모아 **같은 사건끼리 묶고 Top 5를 뽑아**, 로컬 LLM(Ollama)으로 요약·대본을 만든 뒤 **카드뉴스 이미지와 나레이션 음성을 합쳐 세로 영상(mp4)** 으로 만드는 오픈소스 파이프라인입니다. 모든 처리는 내 컴퓨터(Docker)에서 돌아가며 유료 API를 쓰지 않습니다.

> ⚠️ 시작하기 전에 [주의사항](#주의사항-저작권라이선스)을 꼭 읽어 주세요. 특히 **구글 뉴스 RSS 이용 조건**과 **음성 모델(Supertonic)의 사용 제한 조건**이 중요합니다.

## 파이프라인

```
RSS 수집 → 본문 추출 → 같은 사건 묶기 → Top 5 선정
   feedparser   trafilatura    본문 유사도(TF-IDF)    RSS 노출 순서 + 언론사 수
        ↓
   요약(Ollama) → 요약 검토(코드) → 대본(Ollama) → 대본 검사(코드)
        ↓
   카드 이미지(Pillow) + 나레이션 음성(Supertonic 3) → 영상(FFmpeg)
```

| 단계 | 명령 | 입력 → 출력 |
|---|---|---|
| top | `newscard` / `newscard top` | RSS → `data/top_events.json` |
| summarize | `newscard summarize` | Top 5 → `data/summaries.json` |
| verify | `newscard verify` | 요약 검토(코드 검사) → `data/verification.json` |
| script | `newscard script` | 요약 → `data/script.json` |
| check | `newscard verify script` | 대본 검사 → `data/script_check.json` |
| cards | `newscard cards [light\|dark\|both]` | 대본 → `data/cards/` |
| tts | `newscard tts` / `newscard tts samples` | 대본 → `data/audio/` (samples: 목소리 10가지 미리 듣기 → `data/voice_samples/`) |
| video | `newscard video [light\|dark\|both]` | 카드 + 음성 → `data/video/` |
| **전체** | **`newscard run`** | 위 과정을 한 번에 |

## 요구사항

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows는 WSL2 백엔드)
- Git
- 디스크 여유 약 10GB (Docker 이미지, Ollama 모델, 음성 모델 약 400MB)
- 선택: NVIDIA GPU (요약이 훨씬 빨라집니다)

이 문서의 명령은 Windows PowerShell 기준입니다. 반드시 **프로젝트 폴더 안에서** 실행하세요 (`docker-compose.yml`이 있는 곳).

## 빠른 시작

```powershell
git clone <저장소 주소> newscard
cd newscard

# 1) 이미지 만들기 (처음 한 번, 몇 분 걸립니다)
docker compose build

# 2) Ollama 켜고 모델 받기 (처음 한 번, 약 2GB)
docker compose up -d ollama
docker compose exec ollama ollama pull qwen2.5:3b

# 3) 전체 실행
docker compose run --rm app newscard run
```

- 처음 실행하면 음성 모델(Supertonic 3, 약 400MB)이 `data/voices/`로 자동으로 내려받아집니다. 한 번만 받으면 됩니다.
- 기본 목소리는 **F2**(여성)입니다. 목소리는 10가지(여성 F1~F5, 남성 M1~M5)이고, 아래처럼 바꿀 수 있습니다.

### 목소리 바꾸기

```powershell
docker compose run --rm app newscard tts samples            # 같은 문장을 10가지 목소리로 들어 보기 (data/voice_samples/)
docker compose run --rm app newscard tts --voice M2         # 이번 한 번만 M2로 음성 다시 만들기
docker compose run --rm app newscard run --from tts --voice M2   # 음성과 영상만 M2로 다시 만들기 (앞 단계는 건너뜀)
```

계속 다른 목소리를 쓰려면 `.env`에 `NEWSCARD_VOICE=M2`를 적으세요. 우선순위는 `--voice` > `.env`의 `NEWSCARD_VOICE` > 기본값(F2)입니다. 영상은 같은 이름(`data/video/newscard_*.mp4`)으로 덮어써지니, 이전 목소리의 영상을 남기려면 다른 곳으로 복사해 두세요.
- 결과 영상은 `data/video/newscard_light.mp4`, `newscard_dark.mp4` 입니다.
- 한 번 실행에 대략 10~15분을 예상하세요. 대부분 LLM 요약 시간이며 PC 성능에 따라 크게 달라집니다.

### 자주 쓰는 실행 방법

```powershell
docker compose run --rm app newscard run --theme dark      # 다크 모드만
docker compose run --rm app newscard run --from script     # 대본 단계부터 다시
docker compose run --rm app newscard run --until verify    # 요약 검토까지만 (결과 확인용)
docker compose run --rm app newscard run --strict          # 검토에서 문제가 나오면 멈춤
```

단계가 실패하면 그 자리에서 멈추고 `newscard run --from <단계>` 로 이어서 하는 방법을 알려 줍니다. 앞 단계 결과는 `data/`에 남아 있습니다.

### GPU 사용 (NVIDIA)

`.env` 파일(커밋되지 않음)에 한 줄을 추가하면 Ollama가 GPU를 씁니다. Windows는 구분 기호가 `;` 입니다.

```
COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml
```

```powershell
docker compose down
docker compose up -d ollama
docker compose exec ollama ollama ps     # PROCESSOR 칸에 GPU 비율이 보이면 성공
```

## 설정

### 환경변수 (`.env` 또는 `docker compose run -e`)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `NEWSCARD_MODEL` | `qwen2.5:3b` | 요약·대본에 쓰는 Ollama 모델 |
| `NEWSCARD_VOICE` | `F2` | 목소리 `F1`~`F5`(여성), `M1`~`M5`(남성) |
| `NEWSCARD_TTS_SPEED` | (라이브러리 기본 1.05) | 말하는 속도. 클수록 빠름 |
| `NEWSCARD_TTS_STEPS` | (라이브러리 기본 8) | 합성 단계 수. 클수록 품질↑ 속도↓ |
| `NEWSCARD_TTS_NORMALIZE` | `on` | `off`면 숫자·영어를 한글 발음으로 바꾸지 않고 원문 그대로 읽힘 (엔진이 직접 읽는 품질 비교용) |
| `NEWSCARD_BRAND` | `THE DAILY BRIEF` | 카드 상단 이름 |
| `NEWSCARD_FONT`, `NEWSCARD_FONT_BOLD` | (자동 탐색) | 카드에 쓸 폰트 파일 경로 |
| `NEWSCARD_CONFIG` | `config/settings.yaml` | 설정 파일 경로 |

### 설정 파일 `config/settings.yaml`

`config/settings.example.yaml`을 복사해서 바꾸고 싶은 항목만 적습니다. 주요 항목:

| 항목 | 기본값 | 설명 |
|---|---|---|
| `top_n` | 5 | 뽑을 사건 수 |
| `rank_weight` / `source_weight` | 0.85 / 0.15 | 총점 가중치 |
| `source_cap` | 5 | 이 수 이상의 언론사면 언론사 수 점수 만점 |
| `merge_threshold` | 0.30 | 본문 유사도가 이 이상이면 같은 사건 후보 |
| `min_text_chars` | 200 | 본문이 이보다 짧으면 비교하지 않음 |
| `max_related_per_item` | 8 | RSS 항목당 확인할 관련 기사 수 |
| `fetch_timeout` | 15 | 기사 하나를 내려받는 제한 시간(초) |

### 영어 단어 읽기 `config/pronunciations.yaml`

음성 엔진이 영어와 숫자를 어색하게 읽을 수 있어서, 기본 사전(SNS, AI, KBS 등)으로 한글 발음으로 바꿔 줍니다. 사전에 없는 단어는 읽기 전에 경고가 나옵니다. `config/pronunciations.example.yaml`을 복사해서 `단어: 한글 발음` 형식으로 추가하세요.

## 점수 계산

```
총점 = RSS 노출 순서 점수 × 0.85 + 피드에서 확인된 언론사 수 점수 × 0.15
```

- **RSS 노출 순서 점수**: 사건에 속한 대표 기사 중 가장 앞선 순서를 0~1로 환산합니다 (1위=1.0).
- **언론사 수 점수**: 같은 사건으로 확인된 기사의 서로 다른 언론사 수입니다. 1곳=0점, 5곳 이상=1점, 사이는 선형입니다.
- 동점이면 RSS 노출 순서가 앞선 사건이 먼저입니다.
- 이 값은 구글이 공개한 "뉴스 중요도"가 아니라 **RSS 노출 순서**이고, 언론사 수도 **현재 RSS 피드에서 확인된 수**일 뿐 실제 전체 보도 수가 아닙니다.

같은 사건 판단은 기사 본문을 단어로 쪼갠 뒤 TF-IDF 코사인 유사도로 비교합니다. 본문을 못 가져온 기사는 제목 유사도(더 엄격한 기준)로만 보조 판단하고 그 사실을 결과에 표시합니다. 불확실하면 합치지 않습니다.

## 출력 파일 (`data/`)

`data/`는 `.gitignore`에 들어 있어 커밋되지 않습니다.

| 파일/폴더 | 내용 |
|---|---|
| `top_events.json` | Top 사건, 점수, 관련 기사, 본문(기사 본문이 들어 있으니 공개하지 마세요) |
| `cluster_log.json` | 같은 사건으로 판단한 근거 |
| `summaries.json` | 사건별 요약, 출처 |
| `verification.json`, `script_check.json` | 요약·대본 검사 결과 |
| `script.json` | 카드별 제목/줄/나레이션 |
| `cards/light`, `cards/dark`, `cards.json` | 카드 이미지(1080×1920 PNG) |
| `audio/`, `audio.json` | 나레이션 WAV와 실제 길이, 읽은 글 |
| `video/` | 최종 mp4 |
| `run_report.json` | `newscard run`의 단계별 결과 |
| `cache/texts.json` | 내려받은 본문 캐시 (성공한 것만) |
| `voices/` | 내려받은 음성 모델 |

## 프로젝트 구조

```
src/newscard/
  collect.py      RSS 수집, description 속 관련 기사 파싱
  extract.py      구글 뉴스 링크 → 실제 기사 주소, 본문 추출 (trafilatura)
  cluster.py      본문 기반 같은 사건 묶기
  scoring.py      점수 계산, Top N
  pipeline.py     수집 ~ Top N
  summarize.py    요약 (Ollama)           verify.py       요약 코드 검사
  script.py       대본 (Ollama)           script_check.py 대본 코드 검사
  cards.py        카드 이미지 (Pillow)     tts.py          음성 (Supertonic)
  video.py        영상 (FFmpeg)           runner.py       전체 실행
  llm.py          Ollama JSON 호출 도우미   config.py / models.py / cli.py
prompts/          LLM 프롬프트 (코드 밖에 있어 고치기 쉬움)
config/           설정 예시
tests/            테스트
```

## 개발

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

- 테스트는 네트워크와 Ollama 없이 돌아가며, 영상 테스트는 FFmpeg가 있어야 하고 카드 테스트는 한글 폰트가 있어야 합니다(없으면 건너뜁니다).
- `src/`, `prompts/`, `config/`, `data/`는 Docker에 연결돼 있어서 코드를 고치면 이미지를 다시 빌드하지 않아도 반영됩니다. `pyproject.toml`(의존성)을 바꿨을 때만 `docker compose build`가 필요합니다.
- 협업 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 보세요.

## 문제 해결

| 증상 | 해결 |
|---|---|
| `no configuration file provided: not found` | 프로젝트 폴더가 아닌 곳에서 실행한 것입니다. `cd newscard` 후 다시 실행하세요. |
| `Ollama에 연결하지 못했어요` | `docker compose up -d ollama` 로 켜세요. Docker Desktop이 켜져 있는지도 확인하세요. |
| `Ollama에 모델이 없어요` | `docker compose exec ollama ollama pull qwen2.5:3b` |
| 요약이 너무 느림 | GPU 설정을 켜거나, 이미 `3b` 모델입니다. 더 작은 `qwen2.5:1.5b`는 품질이 떨어질 수 있습니다. |
| 본문 추출이 자주 실패 | 일부 언론사가 접근을 막습니다. 우회하지 않고 실패로 기록하며, 해당 기사는 제목으로 보조 판단합니다. |
| 한글이 네모로 나옴 | 폰트를 찾지 못한 것입니다. Docker 이미지는 나눔고딕을 포함합니다. 직접 실행한다면 `assets/fonts/`에 `.ttf`를 넣으세요. |
| 영어 단어 발음이 이상함 | `config/pronunciations.yaml`에 추가하세요. 엔진이 직접 읽게 하려면 `NEWSCARD_TTS_NORMALIZE=off`로 비교해 보세요. |
| 목소리를 바꾸고 싶음 | `newscard tts samples`로 10가지를 들어 보고 `.env`에 `NEWSCARD_VOICE=...` |
| 음성 모델 내려받기 실패 | 인터넷 연결과 디스크 공간(약 400MB)을 확인하세요. 다시 실행하면 이어서 받습니다. |
| `ModuleNotFoundError` | `pip install -e ".[dev]"` (또는 Docker는 `docker compose build`) |

## 알려진 한계

- 구글 뉴스 종합 RSS는 한 번에 약 30~40건이고 시점 스냅샷이라 **지난 7일치를 한 번에 모으지 못합니다.** 실행할 때마다 결과가 달라집니다.
- 같은 사건 판단은 단어 겹침 기반이라 의미를 이해하지 못합니다. 같은 주제를 다른 각도로 쓴 기사는 보수적으로 합쳐지지 않습니다.
- 3B급 로컬 모델의 한국어 문장력에는 한계가 있습니다. 코드 검사가 숫자, 인용문, 영문 이름, 숫자 범위 표현 등을 걸러내지만 **어색한 문장이나 의미 오류를 모두 잡지는 못합니다.** 영상으로 올리기 전에 `data/script.json`과 영상을 사람이 확인하세요.
- 영상에는 자막, 배경 음악, 전환 효과가 없습니다.

## 주의사항 (저작권·라이선스)

1. **구글 뉴스 RSS 이용 조건**: 구글이 RSS 파일에 넣어 둔 안내는 이 피드를 *개인 피드 리더에서 개인적·비영리 목적으로 보는 용도*로만 제공한다고 밝히고 그 외의 사용을 금지한다고 적고 있습니다. 이 프로젝트를 공개 서비스나 수익 목적으로 쓰기 전에 이용 조건을 직접 확인하세요.
2. **뉴스 저작권**: 이 프로젝트는 기사 전문을 재배포하지 않고 요약과 **출처 표기**만 영상에 씁니다. `data/`에는 기사 본문이 들어 있으니 공개하지 마세요. 각 언론사 사이트의 이용 조건과 접근 제한을 지켜야 하며, 접근이 막히면 우회하지 않습니다.
3. **음성 모델 (Supertonic 3)**: 코드는 MIT, 모델은 BigScience **OpenRAIL-M** 라이선스입니다. 비영리 제한은 없지만 *사용 제한 조항*이 있습니다. 예를 들어 불법적인 사용, 해를 끼칠 목적의 허위 정보 생성, 타인을 사칭하는 음성(딥페이크), **기계가 만든 콘텐츠임을 밝히지 않는 사용**이 금지됩니다. 이 프로젝트는 영상과 카드에 AI 요약 고지를 넣어 두었으니 지우지 마세요. 전문은 [모델 저장소의 LICENSE](https://huggingface.co/Supertone/supertonic-3/blob/main/LICENSE)에서 직접 확인하세요. 모델 파일은 저장소에 포함하지 않고 처음 실행할 때 내려받습니다.
4. **AI 요약 고지**: 영상 마지막과 각 뉴스 카드에 AI가 요약했다는 문구가 들어갑니다. 지우지 않는 것을 권장합니다.
5. **폰트**: Docker 이미지는 나눔 글꼴을 설치합니다(배포 시 라이선스를 직접 확인하세요). `assets/fonts/`에 넣는 폰트의 라이선스는 사용자가 확인해야 합니다.
6. **프로젝트 라이선스**: 코드는 GPL-3.0-or-later입니다. 위 1~5번의 외부 자료 조건은 GPL과 별개로 적용됩니다. 자세한 내용은 [라이선스](#라이선스)를 보세요.

## 라이선스

이 프로젝트는 **GNU General Public License v3.0 이상(GPL-3.0-or-later)** 으로 공개합니다. 전문은 [LICENSE](LICENSE)에 있습니다.

- 이 프로그램을 복사, 수정, 재배포할 수 있지만, **수정본을 배포할 때는 같은 GPL 조건으로 소스 코드를 공개**해야 합니다.
- 이 라이선스는 **코드**에만 적용됩니다. 내려받아 쓰는 외부 자료에는 각자의 조건이 따로 있습니다. 위 [주의사항](#주의사항-저작권라이선스)의 한국어 음성 모델(비영리 조건), 폰트, 그리고 이 프로그램으로 가져오는 뉴스 기사와 구글 RSS의 이용 조건이 그렇습니다. GPL이라고 해서 이 자료들을 자유롭게 쓸 수 있게 되는 것은 아닙니다.
- 이 프로그램이 만든 영상 자체의 이용 조건은 GPL이 정하지 않습니다. 영상에 쓰인 음성, 기사 요약, 출처 표기의 조건에 따라 달라집니다.

사용하는 주요 라이브러리의 라이선스는 아래와 같습니다 (PyPI에 표기된 값 기준이며, 배포 전에 직접 확인하세요).

| 라이브러리 | 라이선스 |
|---|---|
| supertonic | MIT (모델은 OpenRAIL-M) |
| trafilatura | Apache-2.0 |
| feedparser | BSD-2-Clause |
| Pillow | MIT-CMU |
| pydantic, ollama, PyYAML, googlenewsdecoder | MIT |
| lxml_html_clean | BSD-3-Clause |

Apache-2.0, MIT, BSD 계열은 GPL-3.0 프로젝트에서 함께 쓸 수 있고, 팀에서 GPL-3.0-or-later로 공개하기로 정했습니다. (법률 자문이 아니므로 중요한 배포 전에는 직접 확인하세요.)
