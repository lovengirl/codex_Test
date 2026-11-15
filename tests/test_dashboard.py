"""임시 DB에서 대시보드 집계·차트 데이터·응답 시간을 검증한다."""

import json
import re
from time import perf_counter

import pytest
from sqlalchemy import event, text

from authentication import COOKIE_NAME, create_token


@pytest.fixture
def dashboard_client(client):
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO employees VALUES (1, '대시보드직원', 'dashboard@example.com', '서울', 'test-hash')"
        ))
    client.cookies.set(COOKIE_NAME, create_token(1, client.app.state.jwt_secret))
    return client


def seed_sales(client):
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO customer VALUES "
            "(1, '고객1', 'one@example.com', '서울', '일반'), "
            "(2, '숨김고객', 'hidden@example.com', '부산', 'VIP'), "
            "(3, '주문없는고객', 'three@example.com', '서울', '신규')"
        ))
        connection.execute(text("INSERT INTO hidden_customer VALUES (2)"))
        connection.execute(text("INSERT INTO product VALUES (1, '상품', '식품', 1000)"))
        connection.execute(text(
            "INSERT INTO orders VALUES "
            "(1, 1, 1, 1, 1, 1000, '2025-01-31', '완료'), "
            "(2, 1, 1, 1, 2, 2500, '2026-01-01 09:00:00', '배송중'), "
            "(3, 1, 1, 1, 3, 3000, '2026-01-31', '완료'), "
            "(4, 1, 1, 1, 1, 1500, '2026-03-01', '완료'), "
            "(5, 1, 1, 1, 1, 999000, '2026-02-01', '취소'), "
            "(6, 2, 1, 1, 1, 888000, '2026-04-01', '완료'), "
            "(7, 2, 1, 1, 1, 777000, '2026-04-02', '취소')"
        ))


def test_실제집계값_카드4종_월별차트_숨김과취소제외(dashboard_client):
    client = dashboard_client
    seed_sales(client)
    response = client.get('/dashboard')
    assert response.status_code == 200
    assert response.context['metrics'] == {
        'customer_count': 2, 'order_count': 4, 'total_sales': 8000, 'average_order': 2000,
    }
    monthly = [
        {'month': '2025-01', 'sales': 1000},
        {'month': '2026-01', 'sales': 5500},
        {'month': '2026-03', 'sales': 1500},
    ]
    assert response.context['monthly_sales'] == monthly
    data = re.search(r'<script id="monthly-sales-data" type="application/json">(.*?)</script>', response.text)
    assert json.loads(data.group(1)) == monthly
    assert response.text.count('<dt>') == 4
    for value in ('2명', '4건', '8,000원', '2,000원', '5,500원', 'new Chart(', "type: 'bar'"):
        assert value in response.text
    assert 'chart.js@4.5.1/dist/chart.umd.min.js' in response.text
    assert response.headers['cache-control'] == 'no-store'
    with client.app.state.engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM orders')) == 7
    print('[통과] 카드 4종과 월별 JSON·표에 DB 집계값 표시, 숨김 고객 및 취소 주문 제외')


@pytest.mark.parametrize('state', ['빈DB', '전체숨김', '전체취소'])
def test_매출없는경우_0과_빈상태(dashboard_client, state):
    client = dashboard_client
    customer_count = 0
    if state != '빈DB':
        seed_sales(client)
        with client.app.state.engine.begin() as connection:
            if state == '전체숨김':
                connection.execute(text('INSERT INTO hidden_customer VALUES (1), (3)'))
            else:
                connection.execute(text("UPDATE orders SET status='취소'"))
                customer_count = 2
    response = client.get('/dashboard')
    assert response.status_code == 200
    assert response.context['metrics'] == {
        'customer_count': customer_count, 'order_count': 0, 'total_sales': 0, 'average_order': 0,
    }
    assert response.context['monthly_sales'] == []
    assert '표시할 매출이 없습니다.' in response.text
    assert '0원' in response.text


def test_모든집계는_DB에서_공통숨김조건_재사용(dashboard_client, monkeypatch):
    from routers import dashboard

    client = dashboard_client
    seed_sales(client)
    common_query = dashboard.visible_customers
    calls = []
    statements = []

    def visible_customers():
        calls.append(True)
        return common_query()

    def capture(connection, cursor, statement, parameters, context, executemany):
        if 'visible_customers AS' in statement:
            statements.append(statement.lower())

    monkeypatch.setattr(dashboard, 'visible_customers', visible_customers)
    event.listen(client.app.state.engine, 'before_cursor_execute', capture)
    try:
        response = client.get('/dashboard')
    finally:
        event.remove(client.app.state.engine, 'before_cursor_execute', capture)
    assert response.status_code == 200
    assert calls == [True]
    assert len(statements) == 3
    assert all('hidden_customer' in sql and 'not (exists' in sql for sql in statements)
    assert 'count(*)' in statements[0]
    assert all(fragment in statements[1] for fragment in ('count(*)', 'sum(', 'avg(', 'status !='))
    assert all(fragment in statements[2] for fragment in ('sum(', 'group by', 'order by', 'status !='))
    print('[통과] 공통 숨김 함수 재사용, COUNT·SUM·AVG·GROUP BY는 SQL에서 수행')


def test_집계는_매요청_변경사항반영(dashboard_client):
    client = dashboard_client
    seed_sales(client)
    assert client.get('/dashboard').context['metrics']['total_sales'] == 8000
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE orders SET status='취소' WHERE id=2"))
    response = client.get('/dashboard')
    assert response.context['metrics']['total_sales'] == 5500
    assert response.context['metrics']['order_count'] == 3
    assert response.context['metrics']['average_order'] == pytest.approx(5500 / 3)
    assert '1,833원' in response.text
    with client.app.state.engine.begin() as connection:
        connection.execute(text('INSERT INTO hidden_customer VALUES (1)'))
    assert client.get('/dashboard').context['metrics']['total_sales'] == 0


def test_주문10만건_카드와차트포함_응답1초이내(dashboard_client):
    client = dashboard_client
    seed_sales(client)
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "WITH RECURSIVE numbers(n) AS (SELECT 10 UNION ALL SELECT n+1 FROM numbers WHERE n<100009) "
            "INSERT INTO orders SELECT n, 1, 1, 1, 1, 1000, '2026-05-01', '완료' FROM numbers"
        ))
    elapsed = []
    for _ in range(3):
        started = perf_counter()
        response = client.get('/dashboard')
        elapsed.append(perf_counter() - started)
        assert response.status_code == 200
        assert response.context['metrics']['order_count'] == 100004
        assert response.context['metrics']['total_sales'] == 100008000
        assert response.context['monthly_sales'][-1] == {'month': '2026-05', 'sales': 100000000}
    assert max(elapsed) < 1, f'최대 응답 시간 {max(elapsed):.3f}초'
    print(f'[통과] 주문 100,007건, 카드·차트 포함 최대 응답 {max(elapsed):.3f}초')


def test_대시보드_인증필수(client):
    response = client.get('/dashboard', headers={'Accept': 'application/json'})
    assert response.status_code == 401
    response = client.get('/dashboard', headers={'Accept': 'text/html'}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'].startswith('/auth/login')
