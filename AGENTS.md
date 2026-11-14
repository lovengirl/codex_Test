# Customer Manager 개발 규칙

## 프로젝트 연속성

- S1부터 S5까지 이 프로젝트를 이어서 수정·확장한다. 기존 코드를 우선 재사용한다.
- 현재 S1은 FastAPI 시작 화면, DB 상태 확인 API, SQLAlchemy 세션, HTTPS 실행 골격이다.
- 인증, 고객 CRUD·검색·필터·페이지, 매출 대시보드·Export, 자연어 질문은 후속 단계에서 구현한다.
- `models.py`의 `SCHEMA`와 실제 `customer.db`를 기준으로 한다.
- `model.py`는 다른 테이블명·칼럼을 사용하는 기존 별도 모델이다. 현재 DB에 적용하거나 자동 생성에 사용하지 않는다.
- `database.py`의 기존 `connect`, `create_tables`를 재사용한다. SQLAlchemy 연결도 `connect`를 통해 외래키 검사를 활성화한다.
- DB 경로는 작업 디렉터리가 아닌 `database.py` 위치를 기준으로 결정한다.
- `main.py`는 앱 생성·시작 및 종료 처리·라우터 등록을 담당한다. 기능별 엔드포인트는 `routers/`에 작성한다.
- `routers/home.py`와 `routers/health.py`는 기존 홈·상태 확인 기능을 제공한다.
- `routers/auth.py`(`/auth`), `routers/customers.py`(`/customers`), `routers/dashboard.py`(`/dashboard`)는 등록된 기능별 라우터이며 실제 업무 API는 후속 단계에서 추가한다.

## 금지 규칙

- 기존 칼럼 삭제, 칼럼 타입 변경, 테이블 삭제·재생성, 데이터 손실을 금지한다.
- 스키마 확장은 nullable 칼럼 또는 새 테이블 추가만 허용한다. 데이터 변경이 필요하면 먼저 백업과 호환성을 검토한다.
- 테스트·샘플 데이터 생성·초기화를 위해 실제 `customer.db`를 삭제하거나 덮어쓰지 않는다.
- 가상환경을 만들거나 사용하지 않는다. 로컬 Python에 설치하고 의존성은 `requirements.txt`로 관리한다.
- 고객 데이터와 `.certs`의 개인키는 Git에 추가하지 않는다.
- 자동 테스트는 임시 DB를 사용한다. 실제 DB 실행 검증은 읽기 조회와 변경 전후 비교만 수행한다.
- 테스트 결과와 사용자에게 전달하는 실행 확인 로그는 한글로 작성한다.

## Git 규칙

- `main` 브랜치에서 직접 작업하거나 커밋하지 않는다. 수정 전에 `git switch -c <종류>/<작업명>`으로 작업 브랜치를 만든다.
- 예: `chore/project-skeleton`, `feat/customer-search`, `fix/database-session`.
- 작업 브랜치에서 테스트 후 커밋하고, PR 검토를 거쳐 `main`에 반영한다.
- 커밋 메시지는 `<종류> : <한글 설명>` 형식을 사용한다. 예: `chore : 프로젝트 골격`.
- 커밋 전에 `git status --short`와 `git diff --cached`로 포함 파일을 확인한다.
- `.gitignore`로 `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, 커버리지 파일, 로그, `.env` 계열 비밀 설정, DB와 인증서를 제외한다. `.env.example`은 공유할 수 있다.
- 고객 DB·개인키·비밀 설정을 `git add -f`로 강제 추가하지 않는다.

## 설치 및 실행

프로젝트 루트에서 PowerShell로 실행한다. 검증 환경은 Windows, Python 3.15이다.
OpenSSL은 PATH 또는 Git for Windows 기본 설치 경로에 있어야 한다.

```powershell
python -m pip install -r requirements.txt
python scripts/create_dev_cert.py
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --ssl-keyfile .certs/localhost.key --ssl-certfile .certs/localhost.crt
```

- 시작 화면: https://127.0.0.1:8000/
- DB 상태: https://127.0.0.1:8000/health
- API 문서: https://127.0.0.1:8000/docs
- 인증서는 로컬 개발용 자체 서명 인증서이며 브라우저에서 신뢰 경고가 표시될 수 있다. 시스템 신뢰 저장소를 자동 변경하지 않는다.
- 인증서 검증을 유지한 CLI 확인: `curl.exe --cacert .certs/localhost.crt https://127.0.0.1:8000/health`
- `python database.py`는 기존 테이블을 유지하고 없는 테이블만 생성한다. 앱 시작 시에도 동일하게 처리한다.
- SQLAlchemy 세션은 `Depends(get_session)`으로 주입하며 쓰기 작업의 커밋은 해당 기능이 명시적으로 처리한다.

### PowerShell에서 uvicorn 명령을 찾지 못하는 경우

Uvicorn이 설치되어 있어도 사용자 Python의 `Scripts` 폴더가 PATH에 없으면
`uvicorn main:app --reload`는 `CommandNotFoundException`으로 실패한다.
이 프로젝트에서는 실행 파일의 PATH에 의존하지 않는 `python -m uvicorn`을 사용한다.

```powershell
# 설치된 Python과 Uvicorn 확인
python -m uvicorn --version
# HTTPS 개발 서버 실행 (소스 수정 시 자동 재시작)
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 --ssl-keyfile .certs/localhost.key --ssl-certfile .certs/localhost.crt
```

`No module named uvicorn`이 나오면 같은 Python으로 `python -m pip install -r requirements.txt`를 실행한다.
TLS 옵션 없이 `python -m uvicorn main:app --reload`로 실행할 때는 `http://127.0.0.1:8000/`로 접속한다.

## 테스트

```powershell
# 홈 화면 200 OK 검증
python -m pytest tests/test_home.py -v
# 기존 unittest 테스트를 포함한 전체 검증
python -m pytest -v
# 실제 HTTPS 서버 검증
python scripts/verify_https.py
# 기존 unittest 방식도 계속 지원
python -m unittest discover -s tests -v
```

HTTPS 검증 명령은 8000 포트가 비어 있을 때 실행한다. Uvicorn을 직접 시작하고 종료하며,
로컬 인증서를 검증한 HTTPS 응답과 기존 DB의 SHA-256·스키마·행 수 보존을 확인한다.
실행 결과는 `verification.log`, Uvicorn 원본 출력은 `uvicorn-verification.log`에서 확인한다.
