"""고객 관리 SQLite DB의 테이블 및 제약조건 정의."""

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS customer (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        address TEXT NOT NULL,
        grade TEXT NOT NULL CHECK (grade IN ('VIP', '일반', '신규'))
    )""",
    """CREATE TABLE IF NOT EXISTS product (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL CHECK (
            category IN ('전자제품', '생활용품', '의류', '식품', '스포츠')
        ),
        price INTEGER NOT NULL CHECK (price BETWEEN 1000 AND 2000000)
    )""",
    """CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        address TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customer(id),
        product_id INTEGER NOT NULL REFERENCES product(id),
        employee_id INTEGER NOT NULL REFERENCES employees(id),
        quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 5),
        total_amount INTEGER NOT NULL CHECK (total_amount >= 1000),
        order_date TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('완료', '배송중', '취소'))
    )""",
)
