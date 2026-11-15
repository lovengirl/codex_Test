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
| 인증 | 직원 회원가입, bcrypt 비밀번호 해시, 만료 60분 JWT 쿠키 |
| 보호 페이지 | `/customers`, `/dashboard` 인증 검사, 공통 접속자 헤더와 로그아웃 |
| 고객 목록 | 페이지당 20명, DB LIMIT/OFFSET 조회, 공통 숨김 고객 제외 |
| 검색·필터 | 이름·이메일 부분 검색, 대소문자 무시, 이름 공백 무시, 등급·지역 필터, URL 조건 유지 |
| 고객 관리 | 등록·수정·숨김 삭제, 상세 주문 이력, 동시 수정 충돌 및 DB 잠금 처리 |
| 매출 대시보드 | 고객 수·주문 수·총매출·평균 주문금액, Chart.js 월별 매출, 숨김 고객·취소 주문 제외 |
| 보고서 다운로드 | 주요 지표·월별 매출을 CSV·Excel·한글 PDF로 다운로드 |
| 자연어 질문 | `gpt-5-mini` Chat Completions, 실제 DB 스키마 전달, 읽기 전용 SELECT와 실행 전 스키마 검증 |
| AI Insight | 기존 집계만 인용한 한국어 요약, 비동기 생성, 추가 SQL 없음, 오류·수치 검증 불일치 시 숨김 |
| 로컬 HTTPS | 개발 인증서 생성 및 실행 검증 도구 |
| 테스트 | 홈 응답, DB 보존, 외래키 검사 및 장애 응답 검증 |

고객 관리, 매출 대시보드·Export, 자연어 질문과 AI Insight를 구현하고 통합 검증했습니다.
전체 테스트는 311개이며 태스크별 완료 상태와 리뷰 반영 내역은 [TODO.md](TODO.md)에서 확인합니다.

고객 목록에서 검색어·등급·지역을 적용하면 조건에 맞는 목록과 페이지네이션을 표시합니다.
조건은 URL과 입력 폼에 유지되어 새로고침하거나 링크를 공유해도 같은 조건으로 조회합니다.
검색어의 `%`와 `_`는 문자 그대로 검색하며, 결과가 없으면 안내 문구를 표시합니다.
숨김 삭제는 `hidden_customer`에 기록하며 기존 고객 행과 주문 이력을 유지합니다.

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
| 회원가입 | https://127.0.0.1:8000/auth/register |
| 로그인 | https://127.0.0.1:8000/auth/login |
| 고객 페이지 | https://127.0.0.1:8000/customers |
| 대시보드 | https://127.0.0.1:8000/dashboard |

인증서는 개발용 자체 서명 인증서입니다. 브라우저에서는 신뢰 경고가 표시될 수 있습니다.

`uvicorn` 명령을 찾지 못하면 위의 `python -m uvicorn` 형식으로 실행합니다.
`No module named uvicorn`이 표시되면 같은 Python으로 의존성을 설치했는지 확인합니다.

자연어 질문과 AI Insight를 사용하려면 프로젝트 루트 `.env`에 `OPENAI_API_KEY`를 설정합니다.
키와 `.env`는 Git에서 제외합니다. 대시보드에서 로그인이 필요하면 로그인 후 원래 대시보드로 복귀합니다.
AI Insight는 화면 표시 후 비동기로 생성되며, 생성·수치 검증 오류 시 카드가 숨겨집니다.
현재 DB에 없는 고객 등록일은 대안 질문으로, 집계 입력에 없는 주간 변화·상위 고객 비중은 확인 불가로 안내합니다.
인증용 `password_hash` 값의 자연어 SQL 조회는 모델 응답과 관계없이 차단합니다.

## 회원가입 및 인증

- 회원가입 폼에 이름·이메일·주소·비밀번호를 입력합니다. 이메일을 로그인 계정으로 사용합니다.
- `POST /auth/register`, `POST /auth/login`은 폼 데이터를 받습니다. 가입 후 로그인 화면으로, 로그인 후 `/customers`로 이동합니다.
- 비밀번호는 bcrypt로 저장합니다. UTF-8 인코딩 기준 1~72바이트를 허용하며, 초과 입력에는 한국어 안내를 표시합니다.
- 중복 이메일 가입은 409 응답과 한국어 안내를 제공합니다. 로그인 실패는 동일한 401 응답과 문구를 사용합니다.
- JWT 유효 기간과 쿠키 유지 시간은 60분입니다. 쿠키는 `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`를 사용합니다. HTTPS로 접속합니다.
- 인증 정보를 조회하는 화면에서 유효한 토큰의 남은 시간이 5분 이내이면 성공 응답의 JWT와 쿠키를 60분으로 갱신합니다. 로그아웃과 새 로그인에서 설정한 쿠키를 우선합니다. `/health`는 인증 쿠키 조회·갱신 없이 DB 상태를 확인합니다.
- 서명키는 `JWT_SECRET_KEY` 환경 변수에서 읽습니다. 설정 시 32바이트 이상을 사용합니다. 기본 실행에서는 DB 옆에 무작위 `customer.jwt-key`를 생성해 재시작 후에도 인증을 유지합니다. 이 파일은 Git에서 제외합니다.
- 홈·고객·대시보드·인증·API 문서 HTML 페이지의 오른쪽 상단에 접속자 이름과 로그아웃 버튼을 표시합니다.
- `POST /auth/logout`은 쿠키를 삭제하고 로그인 화면으로 이동합니다. 보호 페이지의 미인증·만료 요청은 브라우저에서 로그인 화면으로 이동하며 '세션이 만료되었습니다' 안내를 표시합니다. `Accept: application/json` 요청은 기존 401 JSON을 반환합니다.

## 데이터베이스

- `models.py`의 `SCHEMA`와 `database.py`를 재사용합니다.
- 기존 `customer.db`는 프로젝트 루트에 두면 그대로 사용합니다. Git에는 고객 데이터를 올리지 않습니다.
- 새로 복제한 환경에 DB가 없으면 앱 시작 시 빈 DB와 테이블을 생성합니다. 기존 고객 데이터는 포함되어 있지 않습니다.
- 테이블은 `customer`, `hidden_customer`, `product`, `employees`, `orders`입니다.
- 앱 시작과 `python database.py` 실행 시 기존 `employees`에 nullable `password_hash TEXT` 칼럼을 한 번 추가합니다. 기존 직원의 값은 NULL이며 기존 행과 칼럼을 유지합니다.
- 기존 직원 이메일로 가입하면 중복 안내를 표시합니다. 해시가 없는 기존 계정의 로그인은 동일한 로그인 실패 문구로 처리합니다.
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

# 8000번 개발 서버가 실행 중일 때 별도 포트에서 검증
python scripts/verify_https.py --port 18001
```

자동 테스트는 임시 DB를 사용합니다. 가입·로그인 성공과 실패, 토큰 검증, 헤더, 로그아웃,
구버전 DB 확장, 동시 가입 및 재시작 후 인증 유지를 검증합니다.
고객 기능은 10,000명·500페이지·각 응답 1초 이내, 검색·필터와 URL 유지,
등록·수정·숨김 삭제와 주문 보존, 동시 수정 충돌, DB 잠금 및 긴 검색어 처리를 검증합니다.
대시보드 카드·차트·Export 3형식의 값 일치와 갱신, 자연어 SQL 정상 조회·스키마 환각·인증정보 차단,
AI Insight의 추가 SQL 0회·수치 근거·생성 오류 시 생략도 검증합니다.

pytest 임시 DB는 프로젝트의 `.pytest_tmp/run-<실행별 ID>/`에 생성합니다.
사용자 계정이나 동시 실행 간 폴더 권한 충돌을 방지하도록 실행마다 별도 경로를 사용합니다.
`--basetemp`를 직접 지정하면 해당 경로를 우선하며, `.pytest_tmp/`는 Git에서 제외합니다.

HTTPS 검증은 인증서를 생성한 후 8000 포트가 비어 있을 때 실행합니다.
프로젝트 루트의 `customer.db`를 읽기 전용으로 열어 임시 DB에 백업한 뒤 검증용 Uvicorn을 시작·종료합니다.
임시 DB에서 칼럼 확장·기존 전체 행 보존·가입·로그인·헤더·로그아웃을 확인합니다.
원본 DB의 파일 해시·스키마·행 수가 유지되는지도 확인합니다.
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
