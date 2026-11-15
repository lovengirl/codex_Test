# Changelog

이 프로젝트의 주요 변경을 기록합니다.
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 형식을 따르며,
버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 사용합니다.

## [Unreleased]

### Added

### Fixed

### Security

## [1.0.0] - 2026-11-15

첫 정식 릴리즈입니다. 아래 항목은 저장소 커밋 이력을 근거로 작성했습니다.

### Added

- FastAPI 홈·DB 상태 확인·SQLAlchemy 연결·로컬 HTTPS 실행 및 검증 도구를 추가했습니다. (`9ee0508`, `9cc3c93`, `bd36b96`)
- 직원 회원가입·로그인·로그아웃, 로그인 상태별 공통 내비게이션을 추가했습니다. (`e48ea78`, `7df07ae`)
- 고객 등록·수정·숨김 삭제·주문 이력, DB 페이지네이션과 검색·등급·지역 필터를 추가했습니다. (`ae2ecab`, `7776a58`, `c6e83b0`, `13df70b`)
- 고객 수·주문 수·총매출·평균 주문금액과 Chart.js 월별 매출 차트를 추가했습니다. (`1a6e2e6`)
- 대시보드 집계값을 CSV·Excel·한글 PDF로 다운로드하는 기능을 추가했습니다. (`6f8102f`)
- 실제 DB 스키마를 사용하는 `gpt-5-mini` 자연어 질문과 한국어 결과·SQL 표시를 추가했습니다. (`01709af`)
- 기존 집계만 사용하는 비동기 한국어 AI Insight 카드를 추가했습니다. (`079cff4`)
- 카드·차트·Export 통합 검증과 HTTPS 스모크 검증을 확장했습니다. (`7ffcec8`, `e6aeede`)
- Unit·Integration·API 테스트 피라미드, 실제 Chromium 핵심 사용자 여정, Regression 표식 및 분기 포함 Coverage 80% 기준을 도입하고 검증 보고서를 추가했습니다. (`5fd77a9`, `b41043b`)

### Fixed

- 고객 검색의 유니코드 대소문자 처리를 보완했습니다. (`3f5cb77`)
- 세션 만료 시 브라우저 로그인 안내와 API 인증 응답을 구분하고, 만료 임박 토큰을 갱신했습니다. (`c426bea`, `b733cbc`)
- 동시 수정 충돌·이메일 정규화·DB 잠금·조회 스냅샷·검색어 길이 처리를 보완하고 회귀 테스트를 추가했습니다. (`f41dda0`)
- pytest 임시 폴더의 권한 충돌을 실행별 격리 경로로 방지했습니다. (`57fd457`)
- 대시보드 메뉴와 로그인 후 복귀 경로를 수정했습니다. (`8c48fd4`)
- 생성 SQL의 테이블·칼럼을 실행 전에 검증하고, 등록일·탈퇴 등 없는 개념에는 실제 데이터에 근거한 대안 질문을 제시하도록 보완했습니다. (`7f0c3f5`)

### Security

- 직원 비밀번호를 bcrypt 해시로 저장하고 JWT 쿠키 인증을 도입했습니다. (`60842af`, `bd3bc69`, `e48ea78`)
- 자연어 SQL을 읽기 전용 연결과 SELECT 제한으로 실행하도록 구현했습니다. (`01709af`)
- 자연어 SQL에서 직원 `password_hash`의 직접 조회·가공·집계·CTE 우회를 차단했습니다. (`b597aee`)
- AI Insight가 기존 집계 외 수치를 생성하거나 검증에 실패하면 카드를 숨기도록 구현했습니다. (`079cff4`)

[Unreleased]: https://github.com/lovengirl/codex_Test/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/lovengirl/codex_Test/releases/tag/v1.0.0
