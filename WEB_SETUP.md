# 로컬 뉴스 브리핑 웹 연결

이 추가 파일은 기존 `newscard` 프로젝트가 생성한 영상을 웹에서 재생합니다. 웹 서비스는 아래 파일 두 개만 제공하며 `data/`의 기사 본문, 오디오, 모델이나 다른 JSON 파일을 HTTP로 노출하지 않습니다.

- `data/video/newscard_light.mp4`
- `data/video/newscard_dark.mp4`

## 설치

압축 파일의 내용을 `docker-compose.yml`이 있는 기존 프로젝트 폴더에 풀어 다음을 추가합니다. 같은 이름의 파일이 이미 존재하면 덮어쓰기 전에 반드시 백업하고 변경 사항을 비교하세요.

- `web/`: 디자인과 플레이어
- `src/newscard/web_server.py`: 제한된 영상 제공 서버
- `docker-compose.web.yml`: 기존 Compose에 추가할 웹 서비스
- `tests/test_web_server.py`: 웹 서버 테스트
- `WEB_SETUP.md`: 이 안내문

기존 `docker-compose.yml`, `.env`, `data/`, Ollama 모델이나 볼륨은 바꾸지 않습니다.

## 실행 (Windows PowerShell)

기존 프로젝트 폴더에서:

```powershell
docker compose -f docker-compose.yml -f docker-compose.web.yml up -d --build web
```

같은 PC에서 `http://localhost:8000`을 엽니다. 포트가 이미 사용 중이면 현재 PowerShell에서 `NEWSCARD_WEB_PORT`를 설정한 후 다시 실행하세요.

```powershell
$env:NEWSCARD_WEB_PORT="8001"
docker compose -f docker-compose.yml -f docker-compose.web.yml up -d --build web
```

이 경우 `http://localhost:8001`을 사용합니다. 이 설정은 현재 PowerShell 창에만 적용됩니다. 같은 설정으로 정지하려면:

```powershell
docker compose -f docker-compose.yml -f docker-compose.web.yml stop web
```

기본 설정은 **이 PC의 127.0.0.1에서만 접근** 가능합니다. 휴대폰이나 외부 접속은 별도의 네트워크·보안 설정이 필요합니다. 이 웹 서버에는 로그인과 접근 제어가 없으므로 인터넷에 공개하지 마세요.

## 영상 재생

기존 생성 명령 `docker compose run --rm app newscard run` 또는 `newscard video`가 기존 프로젝트의 `data/video/`에 완성 파일을 만들어야 합니다. 웹페이지에서 **영상 확인**을 눌러 새 파일을 확인하세요. 두 영상이 있으면 라이트/다크 전환 시 해당 영상으로 전환하며, 가능하면 재생 위치와 재생 상태를 유지합니다. 브라우저 정책이나 서로 다른 영상 길이 때문에 잠시 로딩되거나 직접 재생을 눌러야 할 수 있습니다. 한 가지 버전만 있으면 다른 모드에서도 그 영상을 재생합니다. 파일이 생성 중이거나 손상된 경우에는 생성이 완료된 뒤 다시 확인하세요.

## 아직 연결되지 않은 기능

- ‘나의 브리핑’ 태그는 브라우저에만 저장됩니다. 서버의 데이터베이스나 뉴스 수집 기능에 연결되어 있지 않습니다.
- 태그별 맞춤 뉴스 수집과 맞춤 영상 생성은 아직 구현하지 않았습니다.
- 이 웹 페이지는 영상을 생성하거나 업로드하지 않습니다. 기존 파이프라인이 생성한 고정 파일을 읽어 재생합니다.

## 폰트와 라이선스

`web/assets/NotoSansKR.ttf`는 Google Fonts의 Noto Sans KR입니다. SIL Open Font License 전문은 `web/assets/OFL.txt`에 있습니다. 기존 프로젝트의 `LICENSE`와 뉴스·음성·영상의 사용 조건도 별도로 확인하세요.
