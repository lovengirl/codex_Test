# 공용 DB 준비 안내

v1.0.0은 모든 실행 환경에서 `models.py`의 공통 스키마를 사용합니다.
고객 데이터가 들어 있는 실제 DB와 인증정보는 저장소·릴리즈에 포함하지 않습니다.
별도 공용 DB 다운로드 주소는 등록되어 있지 않습니다. 처음 실행할 때 아래 명령으로 빈 DB를 준비합니다.

## 새 환경에서 준비

저장소를 복제하고 프로젝트 루트에서 PowerShell로 실행합니다.

```powershell
git clone --branch v1.0.0 https://github.com/lovengirl/codex_Test.git
cd codex_Test
python -m pip install -r requirements.txt
python database.py
python scripts/create_dev_cert.py
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --ssl-keyfile .certs/localhost.key --ssl-certfile .certs/localhost.crt
```

`database.py` 위치를 기준으로 `customer.db`를 생성합니다. 작업 디렉터리가 달라도 DB 위치는 바뀌지 않습니다.
`customer`, `hidden_customer`, `product`, `employees`, `orders` 테이블을 준비하며 앱 시작 시에도 같은 초기화를 수행합니다.

1. `https://127.0.0.1:8000/health`에서 DB 상태를 확인합니다.
2. `/auth/register`에서 직원 계정을 만들고 로그인합니다.
3. `/customers/new`에서 고객을 등록하고 `/dashboard`에서 지표와 Export를 확인합니다.

빈 DB의 매출 지표는 0이며 월별 매출은 빈 상태로 표시됩니다.
주문 등록 UI는 제공하지 않습니다. 매출 시연에는 승인된 기존 DB의 주문 데이터가 필요합니다.
실제 DB의 고객 등록일 칼럼은 없으므로 기간별 신규 고객 수 질문은 대안 안내를 반환합니다.

## 기존 DB를 사용하는 경우

승인된 DB를 사용하기 전에 별도 안전한 위치에 백업하고 프로젝트 루트의 `customer.db`로 준비합니다.
실행 중인 서버를 종료한 상태에서 작업하며, 이미 있는 파일을 자동으로 덮어쓰는 명령은 제공하지 않습니다.
테이블·칼럼 구조는 [models.py](../models.py)를 기준으로 확인합니다. `model.py`의 별도 모델은 사용하지 않습니다.

초기화는 기존 테이블과 행을 보존하며, 필요한 경우 nullable `employees.password_hash` 칼럼을 추가합니다.
해시가 없는 기존 직원은 바로 로그인할 수 없습니다. 기존 이메일과 중복되지 않는 새 직원 계정으로 가입합니다.
기존 직원 이메일로 가입하면 중복 안내가 표시됩니다.

DB와 `.env`, `*.jwt-key`, 인증서 개인키를 Git이나 공개 Release asset에 올리지 않습니다.
테스트·샘플 데이터 생성을 위해 실제 DB를 삭제하거나 덮어쓰지 않습니다.
