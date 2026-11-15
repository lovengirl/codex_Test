# 테스트 피라미드 및 Coverage 보고서

최종 폴더 배치에서 전체 테스트를 실행한 결과다. 기존 테스트 본문과 매개변수를 보존하고 인증·모델 응답의 경계 사례를 보강했다.

## 실행 결과

| 층 | 사례 수 | 결과 |
| --- | ---: | --- |
| `unit` | 337 | 모두 통과 |
| `integration` | 268 | 모두 통과 |
| `api` | 34 | 모두 통과 |

전체 **639개 통과**, 실패·오류·건너뜀 0건. Regression 표식 87건은 전체 실행에 포함한다.

Unit은 함수의 입력·반환값과 경계를, Integration은 실제 SQLite·파일·인증·고객 업무 흐름·Export·모델 어댑터의 결합을 검증한다. API는 HTTP 상태·화면·접근 권한·페이지 경계 계약을 검증한다. 실제 Chromium 사용자 여정은 `tests/integration/browser`의 1건만 유지한다.

## Coverage 요약

| 지표 | 결과 |
| --- | ---: |
| 라인+분기 통합 | 97.67% |
| 라인 | 98.17% (856/872) |
| 분기 | 95.54% (193/202) |
| 통과 기준 | 통합 커버리지 80% 이상 |

`.coveragerc`에서 `branch = True`, `fail_under = 80`을 설정했다. 기준 미달 시 pytest가 비정상 종료한다. 측정 범위는 앱의 모든 활성 Python 모듈과 `routers`다. 테스트·개발 도구 스크립트·사용하지 않는 별도 `model.py`·외부 패키지는 앱 커버리지에 포함하지 않는다. 운영 코드의 줄이나 오류 처리를 커버리지에서 제외하지 않았다.

HTML·JavaScript의 코드 커버리지는 Python 측정값에 포함하지 않는다. 사용자 화면 동작은 실제 브라우저 시나리오로 별도 확인한다. 자동화에서는 외부 OpenAI API 대신 제어된 응답으로 정상·예외·환각을 재현한다.

## 모듈별 커버리지

| 모듈 | 라인+분기 | 미실행 라인 수 | 미실행 분기 수 |
| --- | ---: | ---: | ---: |
| `ai_insight.py` | 100.00% | 0 | 0 |
| `authentication.py` | 98.28% | 1 | 1 |
| `customer_queries.py` | 97.73% | 0 | 1 |
| `dashboard_exports.py` | 98.43% | 1 | 1 |
| `database.py` | 94.12% | 2 | 1 |
| `main.py` | 100.00% | 0 | 0 |
| `models.py` | 100.00% | 0 | 0 |
| `natural_language.py` | 100.00% | 0 | 0 |
| `routers/__init__.py` | 100.00% | 0 | 0 |
| `routers/auth.py` | 94.39% | 5 | 1 |
| `routers/customers.py` | 93.15% | 7 | 3 |
| `routers/dashboard.py` | 98.33% | 0 | 1 |
| `routers/health.py` | 100.00% | 0 | 0 |
| `routers/home.py` | 100.00% | 0 | 0 |
| `templating.py` | 100.00% | 0 | 0 |

## 80% 미달 및 남은 미검증 영역

**80% 미달 모듈은 없다.** 아래는 기준을 충족한 모듈에도 남아 있는 미실행 라인·분기다.

| 모듈 | 미실행 라인 | 미실행 분기(시작→목적지) |
| --- | --- | --- |
| `authentication.py` | 94 | 93→94 |
| `customer_queries.py` | 없음 | 41→43 |
| `dashboard_exports.py` | 106 | 105→106 |
| `database.py` | 80, 81 | 79→80 |
| `routers/auth.py` | 93, 94, 99, 118, 119 | 98→99 |
| `routers/customers.py` | 104, 105, 109, 169, 170, 175, 193 | 108→109, 174→175, 192→193 |
| `routers/dashboard.py` | 없음 | 30→32 |

주요 잔여 영역은 손상된 로컬 JWT 서명키 파일, 한글 글꼴 미설치 환경, DB 초기화 CLI 진입점, 트랜잭션의 드문 무결성 충돌과 잠금 이외 DB 예외 전파 경로다. 미검증은 해당 실행에서 경로를 밟지 않았다는 의미이며 오류 발견을 뜻하지 않는다. 기존 잠금·동시 변경·데이터 보존 회귀 테스트는 유지한다.

## Regression 목록

| 기존 버그 | 고정한 검증 | 위치 |
| --- | --- | --- |
| 와일드카드 | `%`·`_`를 문자 그대로 검색, 공백 제거와 LIKE 이스케이프, SQL 삽입 문자 | `tests/integration/test_customers.py` |
| JWT 만료 | 브라우저 로그인 이동, API 401, 만료 직전 갱신, 잘못된 서명·식별자 | `tests/integration/test_auth.py`, `tests/unit/test_security_boundaries.py` |
| SQL 환각 | 없는 테이블·칼럼 실행 전 거부, 등록일 부재·탈퇴→숨김 대안, 비밀 칼럼 우회 차단 | `tests/integration/test_sql_execution.py`, `tests/integration/test_natural_language.py` |
| Insight 환각 | 새 숫자·없는 입력·틀린 단위·불완전 JSON 거부, 오류 시 카드 생략 | `tests/unit/test_ai_insight.py`, `tests/integration/test_ai_insight.py` |

## 재현 명령

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m pytest tests/api -q
python -m pytest -m regression -q
python -m pytest --cov --cov-report=term-missing --cov-report=html --cov-report=json:.pytest_tmp/coverage.json --cov-report=xml:.pytest_tmp/coverage.xml -q
```

전체 Coverage 명령은 브라우저 테스트도 포함한다. 각 층의 개별 실행에는 80% 전체 앱 기준을 강제하지 않고, 전체 Coverage 실행에 적용한다.

상세 산출물: `htmlcov/index.html`, `.pytest_tmp/coverage.json`, `.pytest_tmp/coverage.xml`. 이 파일들과 테스트 DB·인증서·추적 로그는 Git에서 제외한다.
