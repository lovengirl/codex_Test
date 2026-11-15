"""홈·공통 내비게이션과 상태 확인 JSON을 임시 DB에서 검증한다."""

import pytest
from sqlalchemy import text

from authentication import COOKIE_NAME, create_token


@pytest.fixture
def signed_in_client(client):
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO employees (id, name, email, address, password_hash) "
            "VALUES (1, '화면검증직원', 'navigation@example.com', '서울', 'test-hash')"
        ))
        connection.execute(text(
            "INSERT INTO customer VALUES (1, '화면검증고객', 'customer@example.com', '부산', '신규')"
        ))
    client.cookies.set(COOKIE_NAME, create_token(1, client.app.state.jwt_secret),
                       domain="testserver.local", path="/")
    return client


def test_home_returns_200(client):
    """홈 화면이 200 OK와 HTML 본문을 반환한다."""
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Customer Manager" in response.text


def test_홈_비로그인_로그인유도_로그인후_인사와목록바로가기(signed_in_client):
    client = signed_in_client
    page = client.get("/")
    main = page.text.partition('<main class="">')[2].partition("</main>")[0]
    assert "화면검증직원님, 환영합니다." in main
    assert '<a class="home-primary" href="/customers">고객 목록 바로가기</a>' in main
    assert "로그인해 주세요" not in main
    assert client.post("/auth/logout", follow_redirects=False).status_code == 303
    page = client.get("/")
    assert "고객 관리를 시작하려면 로그인해 주세요." in page.text
    assert '<a class="home-primary" href="/auth/login">로그인하기</a>' in page.text
    assert "화면검증직원" not in page.text


@pytest.mark.parametrize("path,active", [
    ("/", "/"), ("/customers", "/customers"), ("/customers?page=1", "/customers"),
    ("/customers/new", "/customers"), ("/customers/1", "/customers"),
    ("/customers/1/edit", "/customers"), ("/dashboard", None),
    ("/auth/login", None), ("/auth/register", None), ("/docs", None), ("/redoc", None),
])
def test_모든화면_base상속_공통헤더_활성메뉴(signed_in_client, path, active):
    response = signed_in_client.get(path)
    assert response.status_code == 200
    assert response.template.name in {
        "home.html", "customers.html", "customer_form.html", "customer_detail.html",
        "dashboard.html", "auth.html", "documentation.html",
    }
    assert response.text.count('<header class="site-header">') == 1
    assert response.text.count("<main ") == 1
    assert response.text.count("<title>") == 1
    header = response.text.partition('<header class="site-header">')[2].partition("</header>")[0]
    assert "홈</a>" in header and "고객 목록</a>" in header
    assert "화면검증직원님 안녕하세요" in header
    assert header.count('action="/auth/logout"') == 1
    assert response.headers["cache-control"] == "no-store"
    if active:
        assert f'href="{active}" aria-current="page"' in header
        assert header.count('aria-current="page"') == 1
    else:
        assert 'aria-current="page"' not in header


@pytest.mark.parametrize("path", ["/", "/auth/login", "/auth/register", "/docs", "/redoc"])
def test_비로그인_공통헤더_로그인링크와회원가입(client, path):
    response = client.get(path)
    assert response.status_code == 200
    header = response.text.partition('<header class="site-header">')[2].partition("</header>")[0]
    assert '<a href="/auth/login">로그인</a>' in header
    assert '<a href="/auth/register">회원가입</a>' in header
    assert "안녕하세요" not in header
    assert 'action="/auth/logout"' not in header


@pytest.mark.parametrize("path,assets", [
    ("/docs", ["swagger-ui.css", "swagger-ui-bundle.js", "SwaggerUIBundle", "url: '/openapi.json'"]),
    ("/redoc", ['<redoc spec-url="/openapi.json">', "redoc.standalone.js"]),
])
def test_API문서_base상속후_스크립트와스키마주소보존(client, path, assets):
    response = client.get(path)
    for asset in assets:
        assert asset in response.text
    assert response.text.count("<html") == 1
    assert response.text.count("<body>") == 1


def test_상태확인은_health_JSON_루트는_HTML(client):
    home = client.get("/", headers={"Accept": "application/json"})
    assert home.status_code == 200
    assert home.headers["content-type"].startswith("text/html")
    health = client.get("/health")
    assert health.status_code == 200
    assert health.headers["content-type"].startswith("application/json")
    assert health.json() == {"status": "ok", "database": "ok"}
