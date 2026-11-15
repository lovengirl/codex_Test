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
| 테스트 | 카드·차트·Export 값 비교, 자연어 SQL·Insight 회귀 검증, 인증·고객 관리·DB 보존 검증 |

고객 관리, 매출 대시보드·Export, 자연어 질문과 AI Insight를 구현하고 통합 검증했습니다.
테스트는 Unit·Integration·API 피라미드로 구성하며 태스크별 완료 상태와 리뷰 반영 내역은 [TODO.md](TODO.md)에서 확인합니다.

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

같은 포트에는 서버를 하나만 실행합니다. 이미 실행한 서버는 해당 터미널에서 `Ctrl+C`로 종료합니다.
8000번 포트에서 `WinError 10013` 등으로 실행할 수 없으면 다른 포트로 실행합니다.

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --ssl-keyfile .certs/localhost.key --ssl-certfile .certs/localhost.crt
```

이 경우 대시보드 주소는 `https://127.0.0.1:8001/dashboard`입니다.

자연어 질문과 AI Insight를 사용하려면 프로젝트 루트 `.env`에 `OPENAI_API_KEY`를 설정합니다.
키와 `.env`는 Git에서 제외합니다. 대시보드에서 로그인이 필요하면 로그인 후 원래 대시보드로 복귀합니다.
AI Insight는 화면 표시 후 비동기로 생성되며, 생성·수치 검증 오류 시 카드가 숨겨집니다.
현재 DB에 없는 고객 등록일은 대안 질문으로, 집계 입력에 없는 주간 변화·상위 고객 비중은 확인 불가로 안내합니다.
인증용 `password_hash` 값의 자연어 SQL 조회는 모델 응답과 관계없이 차단합니다.

`.env` 설정 예시는 다음과 같습니다. 실제 발급받은 키로 값을 바꾸고 파일을 저장합니다.

```dotenv
OPENAI_API_KEY=여기에_발급받은_API_키
```

VS Code의 `.vscode/settings.json`에는 Python 터미널의 환경 파일 로딩 설정이 포함되어 있습니다.
환경 설정을 바꾼 뒤에는 개발 서버를 다시 시작합니다.

## 대시보드 사용 방법

1. 회원가입 후 로그인하고 상단 **대시보드** 메뉴를 엽니다.
2. 지표 카드 4개와 월별 매출 차트를 확인합니다. 집계는 DB의 `COUNT`, `SUM`, `AVG`, `GROUP BY`로 처리합니다.
3. 다운로드 영역에서 **CSV**, **Excel**, **PDF**를 선택하면 주요 지표와 월별 매출 보고서를 받습니다.
4. 상단 질문창에 자연어 질문을 입력하면 한국어 답변, 조회 결과, 생성된 SQL을 함께 표시합니다.
5. AI Insight는 기존 집계값만 사용해 2~3문장으로 표시합니다. 생성 오류가 나면 카드를 숨깁니다.

자연어 질문은 `gpt-5-mini`의 `chat.completions.create`와 JSON 응답 형식을 사용합니다.
런타임에 `sqlite_master`와 `PRAGMA table_xinfo`로 전체 테이블·칼럼을 조회하고,
생성된 SQL은 실제 스키마와 대조한 뒤 읽기 전용 연결에서 실행합니다.
공백·주석·소문자·세미콜론이 포함된 정상 SELECT를 허용하며 데이터 변경 구문은 차단합니다.
API 호출·SQL 처리 오류는 예외 타입과 메시지를 화면과 콘솔에 표시합니다.

예를 들어 **이번 달 매출**은 주문일·금액을 집계합니다.
**2026년 신규 고객수**는 현재 DB에 고객 등록일이 없어 숫자를 생성하지 않고 대안을 안내합니다.
**탈퇴 고객 수**처럼 직접 대응하는 데이터가 없는 질문에는 숨김 고객 수 조회를 제안합니다.
전주 대비 변화와 상위 고객 비중도 기존 집계에 없으므로 Insight에서 확인 불가로 표시합니다.

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
# 함수 단위: 가장 많은 사례, HTTP·외부 API 호출 없음
python -m pytest tests/unit -q

# Integration: 실제 DB·파일·모듈 결합과 업무 흐름
python -m pytest tests/integration -q

# API: HTTP 응답 계약, 인증·출처·페이지 경계
python -m pytest tests/api -q

# E2E 최초 준비: 실제 Chromium 설치
python -m playwright install chromium

# 실제 HTTPS 브라우저: 로그인 → 고객 등록 → 검색 → 로그아웃
python -m pytest tests/integration/browser -q -s

# 전체 테스트: 기존 unittest 포함
python -m pytest -v

# 기존 버그 회귀 검증만 선택
python -m pytest -m regression -q

# 앱 전체 라인·분기 커버리지, 80% 미만이면 비정상 종료
python -m pytest --cov --cov-report=term-missing --cov-report=html --cov-report=json:.pytest_tmp/coverage.json --cov-report=xml:.pytest_tmp/coverage.xml -q

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

기존 311개 테스트의 검증을 보존하면서 세 층으로 재구성하고 사례를 추가했습니다.

| 층 | 실행 명령 | 검증 범위 |
| --- | --- | --- |
| Unit | `python -m pytest tests/unit -q` | 함수 입력·반환값·경계 사례 |
| Integration | `python -m pytest tests/integration -q` | 실제 SQLite·인증·고객 업무 흐름·보고서·모델 어댑터 |
| API | `python -m pytest tests/api -q` | HTTP 상태·화면·접근 권한·페이지 경계 계약 |

전체 실행 결과와 모듈별 커버리지·미검증 영역은 [Coverage 보고서](docs/COVERAGE.md)에 기록합니다.
HTML 상세 보고서는 실행 후 `htmlcov/index.html`, 기계 판독 결과는 `.pytest_tmp/coverage.json`과 `.pytest_tmp/coverage.xml`에 생성됩니다.

별도 포트의 HTTPS 검증에서 대시보드 응답은 **0.207초**였으며,
카드·차트와 CSV·Excel·PDF의 값을 비교하고 원본 DB 보존도 확인했습니다.
리뷰에서 발견된 직원 비밀번호 해시 조회 경로는 차단하고 회귀 테스트를 추가했습니다.
성능 수치는 검증 당시 환경의 측정값이며, 자연어 질문과 Insight의 외부 API 응답 시간은 별도입니다.

Unit은 고객 입력·검색 정규화·JSON 파싱·SQL 허용/차단·Insight 수치 검증 등 함수의 입력과 반환값을 검증합니다.
할인 계산은 현재 구현된 기능에 없으므로 실제 존재하는 검증 로직에 집중합니다.
API는 `TestClient`로 200·401·403 및 첫 페이지·마지막 페이지·범위 밖 응답까지 검증합니다.
Integration은 DB 저장·숨김·동시 수정·집계·Export·SQL 실행의 모듈 간 결합을 검증합니다.
여러 요청과 실제 DB를 함께 사용하는 업무 시나리오는 Integration에 두고, API에는 HTTP 계약 검증을 유지합니다.
`tests/integration/browser`는 Playwright의 실제 Chromium으로 핵심 사용자 여정 1건만 실행합니다.
매번 임시 DB·인증서·자동 할당 HTTPS 포트를 사용하고 서버와 브라우저를 종료하므로 실행 중인 개발 서버와 분리됩니다.
자체 서명 인증서 허용은 테스트 브라우저 컨텍스트에만 적용합니다.
실패 시 스크린샷과 추적 ZIP을 해당 `.pytest_tmp/` 폴더에 저장합니다.
각 실행 끝에는 한글 층별 요약을 출력하며 `-m unit`, `-m integration`, `-m api`로도 선택할 수 있습니다.
`-m regression`은 와일드카드·JWT 만료·스키마/Insight 환각 회귀 사례, `-m browser`는 브라우저 시나리오를 선택합니다.
자동 테스트는 실제 OpenAI 호출 대신 제어된 모델 응답으로 정상·오류·환각을 재현합니다.

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
├── authentication.py       # JWT 쿠키 인증과 서명키 관리
├── customer_queries.py     # 공통 숨김 고객 제외
├── natural_language.py     # 실제 스키마 기반 SQL 생성·검증·읽기 전용 실행
├── dashboard_exports.py    # CSV·Excel·한글 PDF 보고서
├── ai_insight.py           # 기존 집계 기반 비동기 Insight
├── requirements.txt
├── routers/
│   ├── home.py
│   ├── health.py
│   ├── auth.py
│   ├── customers.py
│   └── dashboard.py
├── templates/              # 질문창·다운로드·Insight를 포함한 화면
├── scripts/
│   ├── create_dev_cert.py
│   └── verify_https.py
├── tests/
│   ├── conftest.py
│   ├── unit/               # 함수 검증: 입력·SQL·Insight·보고서
│   ├── integration/        # SQLite·업무 흐름·보고서·모델 어댑터
│   │   └── browser/        # 실제 Chromium 핵심 사용자 여정
│   └── api/                # HTTP 계약: 상태·접근 권한·페이지 경계
├── pytest.ini              # 층별 표식과 테스트 탐색 설정
├── .coveragerc             # 앱 전체 측정 범위, 분기 포함 80% 기준
├── docs/COVERAGE.md        # 커버리지 요약과 미검증 영역
├── TODO.md                 # 태스크별 진행률과 검증 결과
└── AGENTS.md               # 실행·테스트·개발 규칙
```

## Git 규칙

- `main`에서 직접 작업하거나 커밋하지 않습니다. 작업 브랜치에서 수정하고 테스트 후 PR로 반영합니다.
- 변경은 의미 단위로 나눠 커밋합니다. 예: `docs : 프로젝트 README 추가`.
- DB, 인증서·개인키, 비밀 설정, 캐시, 로그는 커밋하지 않습니다.
- 세부 규칙은 [AGENTS.md](AGENTS.md)를 따릅니다.
