"""임시 DB에서 대시보드 집계·차트 데이터·응답 시간을 검증한다."""

import json
import csv
from io import BytesIO, StringIO
import re
from time import perf_counter

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import event, text

from authentication import COOKIE_NAME, create_token
from dashboard_exports import dashboard_report


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


def test_다운로드영역에_CSV_Excel_PDF버튼표시(dashboard_client):
    response = dashboard_client.get('/dashboard')
    assert '보고서 다운로드' in response.text
    for extension, label in [('csv', 'CSV'), ('xlsx', 'Excel'), ('pdf', 'PDF')]:
        assert f'href="/dashboard/export/{extension}">{label} 다운로드</a>' in response.text
    assert response.text.index('보고서 다운로드') < response.text.index('name="question"')


@pytest.mark.parametrize('extension,content_type', [
    ('csv', 'text/csv'),
    ('xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    ('pdf', 'application/pdf'),
])
def test_다운로드_실제파일형식과_숨김취소제외_집계값일치(dashboard_client, extension, content_type):
    client = dashboard_client
    seed_sales(client)
    response = client.get(f'/dashboard/export/{extension}')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith(content_type)
    assert response.headers['content-disposition'] == f'attachment; filename="dashboard-report.{extension}"'
    assert response.headers['cache-control'] == 'no-store'
    if extension == 'csv':
        assert response.content.startswith(b'\xef\xbb\xbf')
        rows = list(csv.reader(StringIO(response.content.decode('utf-8-sig'))))
        assert rows == [
            ['구분', '항목', '값', '단위'],
            ['주요 지표', '고객 수', '2', '명'], ['주요 지표', '주문 수', '4', '건'],
            ['주요 지표', '총매출', '8000', '원'], ['주요 지표', '평균 주문금액', '2000.0', '원'],
            ['월별 매출', '2025-01', '1000', '원'], ['월별 매출', '2026-01', '5500', '원'],
            ['월별 매출', '2026-03', '1500', '원'],
        ]
    elif extension == 'xlsx':
        workbook = load_workbook(BytesIO(response.content))
        try:
            sheet = workbook['대시보드']
            assert [sheet.cell(row, 3).value for row in range(6, 13)] == [2, 4, 8000, 2000, 1000, 5500, 1500]
            assert sheet['C8'].data_type == 'n'
            assert sheet['C8'].number_format == '#,##0'
            assert sheet['B11'].value == '2026-01'
            assert sheet.freeze_panes == 'C6'
            assert sheet.auto_filter.ref == 'A5:D12'
        finally:
            workbook.close()
    else:
        assert response.content.startswith(b'%PDF-')
        pdf = PdfReader(BytesIO(response.content))
        pdf_text = '\n'.join(page.extract_text() for page in pdf.pages)
        for value in ('매출 대시보드 보고서', '고객 수', '총매출', '8,000', '2026-01', '5,500', '1,500'):
            assert value in pdf_text
        assert '999,000' not in pdf_text and '888,000' not in pdf_text
    with client.app.state.engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM orders')) == 7
    print(f'[통과] {extension} 다운로드 형식·한글·지표·월별 매출 일치, 원본 주문 7건 보존')


@pytest.mark.parametrize('extension', ['csv', 'xlsx', 'pdf'])
def test_빈대시보드도_정상파일다운로드(dashboard_client, extension):
    response = dashboard_client.get(f'/dashboard/export/{extension}')
    assert response.status_code == 200
    if extension == 'csv':
        rows = list(csv.reader(StringIO(response.content.decode('utf-8-sig'))))
        assert len(rows) == 5
        assert all(float(row[2]) == 0 for row in rows[1:])
    elif extension == 'xlsx':
        workbook = load_workbook(BytesIO(response.content))
        try:
            assert workbook.active.max_row == 9
            assert workbook.active['C8'].value == 0
        finally:
            workbook.close()
    else:
        assert '총매출' in PdfReader(BytesIO(response.content)).pages[0].extract_text()


@pytest.mark.parametrize('extension', ['csv', 'xlsx', 'pdf'])
def test_보고서다운로드_인증필수(client, extension):
    response = client.get(f'/dashboard/export/{extension}', headers={'Accept': 'application/json'})
    assert response.status_code == 401


def test_지원하지않는다운로드형식은_422(dashboard_client):
    assert dashboard_client.get('/dashboard/export/exe').status_code == 422


def test_CSV와Excel에서_월문자열을_수식으로실행하지않음(dashboard_client):
    client = dashboard_client
    seed_sales(client)
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE orders SET order_date='=1+1' WHERE id=1"))
    csv_response = client.get('/dashboard/export/csv')
    assert "'=1+1" in csv_response.content.decode('utf-8-sig')
    workbook = load_workbook(BytesIO(client.get('/dashboard/export/xlsx').content))
    try:
        cell = next(cell for row in workbook.active for cell in row if cell.value == '=1+1')
        assert cell.data_type == 's'
        assert all(cell.data_type != 'f' for row in workbook.active for cell in row)
    finally:
        workbook.close()


def test_PDF여러페이지에서도_전체월과_한글헤더유지():
    data = {'metrics': {'customer_count': 1, 'order_count': 120, 'total_sales': 120000, 'average_order': 1000},
            'monthly_sales': [{'month': f'{2000+index//12}-{index%12+1:02}', 'sales': 1000} for index in range(120)]}
    pdf = PdfReader(BytesIO(dashboard_report(data, 'pdf')))
    assert len(pdf.pages) > 1
    assert all('구분' in page.extract_text() for page in pdf.pages)
    assert '2009-12' in pdf.pages[-1].extract_text()


@pytest.mark.parametrize('extension', ['csv', 'xlsx', 'pdf'])
def test_카드_차트_Export는_같은집계이며_숨김직후값도갱신(dashboard_client, extension):
    client = dashboard_client
    seed_sales(client)
    page = client.get('/dashboard')
    assert '<dt>고객 수</dt><dd>2명</dd>' in page.text
    assert '<dt>주문 수</dt><dd>4건</dd>' in page.text
    assert '<dt>총매출</dt><dd>8,000원</dd>' in page.text
    chart = json.loads(re.search(
        r'<script id="monthly-sales-data" type="application/json">(.*?)</script>', page.text,
    ).group(1))
    assert sum(item['sales'] for item in chart) == page.context['metrics']['total_sales']
    assert client.post('/customers/1/delete', follow_redirects=False).status_code == 303
    page = client.get('/dashboard')
    assert page.context['metrics'] == {
        'customer_count': 1, 'order_count': 0, 'total_sales': 0, 'average_order': 0,
    }
    assert page.context['monthly_sales'] == []
    assert '표시할 매출이 없습니다.' in page.text
    report = client.get(f'/dashboard/export/{extension}')
    assert report.status_code == 200
    if extension == 'csv':
        rows = list(csv.reader(StringIO(report.content.decode('utf-8-sig'))))
        assert [float(row[2]) for row in rows[1:]] == [1, 0, 0, 0]
        assert len(rows) == 5
    elif extension == 'xlsx':
        workbook = load_workbook(BytesIO(report.content))
        try:
            assert [workbook.active.cell(row, 3).value for row in range(6, 10)] == [1, 0, 0, 0]
            assert workbook.active.max_row == 9
        finally:
            workbook.close()
    else:
        pdf_text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(report.content)).pages)
        assert '고객 수' in pdf_text and '총매출' in pdf_text
        assert '8,000' not in pdf_text and '2025-01' not in pdf_text
    with client.app.state.engine.connect() as connection:
        assert connection.scalar(text('SELECT COUNT(*) FROM orders')) == 7
    print(f'[통과] 카드·차트·{extension} 동일 집계, 고객 숨김 직후 반영, 원본 주문 7건 보존')
