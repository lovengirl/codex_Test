# Customer Manager

Python, FastAPI, SQLAlchemy로 만드는 고객관리 웹 서비스입니다. S1부터 S5까지 기존 코드를 이어서 확장합니다.

## 현재 구현 범위

| 기능 | 상태 |
| --- | --- |
| 홈 화면 | HTML 응답, 200 OK 검증 |
| 상태 확인 | `/health`에서 실제 고객 테이블 조회 및 DB 연결 확인 |
| API 문서 | `/docs`, `/openapi.json` 제공 |
| DB 연결 | 기존 SQLite 연결 재사용, SQLAlchemy 세션 및 외래키 검사 |
| 라우터 | 홈·상태 확인 분리, 인증·고객·대시보드 확장 구조 등록 |
| 로컬 HTTPS | 개발 인증서 생성 및 실행 검증 도구 |
| 테스트 | 홈 응답, DB 보존, 외래키 검사 및 장애 응답 검증 |

인증, 고객 CRUD·검색·필터·페이지, 매출 대시보드·Export, 자연어 질문은 후속 단계에서 구현합니다.
인증·고객·대시보드 라우터에는 아직 실제 업무 API가 없습니다.

## 설치 및 실행

검증 환경은 Windows PowerShell, Python 3.15.2입니다. 가상환경 없이 로컬 Python에 설치합니다.
인증서 생성에는 OpenSSL이 필요합니다. PATH 또는 Git for Windows 기본 설치 경로에서 찾습니다.

```powershell
python -m pip install -r requirements.txt
python scripts/create_dev_cert.py
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 --ssl-keyfile .certs/localhost.key --ssl-certfile .certs/localhost.crt
```

| 경로 | 주소 |
| --- | --- |
| 홈 | https://127.0.0.1:8000/ |
| 상태 확인 | https://127.0.0.1:8000/health |
| API 문서 | https://127.0.0.1:8000/docs |

인증서는 개발용 자체 서명 인증서입니다. 브라우저에서는 신뢰 경고가 표시될 수 있습니다.

`uvicorn` 명령을 찾지 못하면 위의 `python -m uvicorn` 형식으로 실행합니다.
`No module named uvicorn`이 표시되면 같은 Python으로 의존성을 설치했는지 확인합니다.

## 데이터베이스

- `models.py`의 `SCHEMA`와 `database.py`를 재사용합니다.
- 기존 `customer.db`는 프로젝트 루트에 두면 그대로 사용합니다. Git에는 고객 데이터를 올리지 않습니다.
- 새로 복제한 환경에 DB가 없으면 앱 시작 시 빈 DB와 테이블을 생성합니다. 기존 고객 데이터는 포함되어 있지 않습니다.
- 테이블은 `customer`, `product`, `employees`, `orders`입니다.
- 기존 칼럼 삭제·타입 변경·데이터 손실을 금지합니다. nullable 칼럼과 새 테이블 추가만 허용합니다.
- `model.py`는 실제 DB와 구조가 다른 별도 기존 모델로, 현재 앱에서는 사용하지 않습니다.

## 테스트

```powershell
# 첫 pytest: 홈 화면 200 OK
python -m pytest tests/test_home.py -v

# 전체 테스트: 기존 unittest 포함
python -m pytest -v

# 실제 HTTPS 서버와 기존 DB 보존 확인
python scripts/verify_https.py
```

자동 테스트는 임시 DB를 사용합니다. 전체 테스트 4개가 통과했으며, 현재 테스트 클라이언트의
`httpx` 사용에 대한 지원 예정 중단 경고가 1건 있습니다.

HTTPS 검증은 인증서를 생성한 후 8000 포트가 비어 있을 때 실행합니다.
프로젝트 루트의 `customer.db`를 대상으로 검증용 Uvicorn을 시작·종료하고,
인증서를 검증한 HTTPS 응답과 DB 파일 해시·스키마·행 수가 유지되는지 확인합니다.
DB가 없다면 먼저 `python database.py`로 생성합니다.
한글 결과는 `verification.log`, 서버 원본 출력은 `uvicorn-verification.log`에 저장됩니다.

## 프로젝트 구조

```text
customer-manager/
├── main.py                 # 앱 생성, 시작·종료 처리, 라우터 등록
├── database.py             # SQLite 연결, 초기화, SQLAlchemy 세션
├── models.py               # 실제 DB의 스키마 정의
├── model.py                # 현재 앱에서 사용하지 않는 별도 모델
├── requirements.txt
├── routers/
│   ├── home.py
│   ├── health.py
│   ├── auth.py
│   ├── customers.py
│   └── dashboard.py
├── scripts/
│   ├── create_dev_cert.py
│   └── verify_https.py
├── tests/
│   ├── conftest.py
│   ├── test_home.py
│   └── test_app.py
└── AGENTS.md               # 실행·테스트·개발 규칙
```

## Git 규칙

- `main`에서 직접 작업하거나 커밋하지 않습니다. 작업 브랜치에서 수정하고 테스트 후 PR로 반영합니다.
- 변경은 의미 단위로 나눠 커밋합니다. 예: `docs : 프로젝트 README 추가`.
- DB, 인증서·개인키, 비밀 설정, 캐시, 로그는 커밋하지 않습니다.
- 세부 규칙은 [AGENTS.md](AGENTS.md)를 따릅니다.

