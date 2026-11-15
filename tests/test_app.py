"""실제 고객 DB 대신 임시 DB에서 시작 동작과 연결을 검증한다."""

from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import text

from database import connect, initialize_database
from main import create_app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"

    def test_health_and_docs(self):
        """DB 상태 API와 API 문서가 정상 응답한다."""
        with TestClient(create_app(self.path)) as client:
            response = client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok", "database": "ok"})
            self.assertEqual(client.get("/docs").status_code, 200)

    def test_existing_data_and_schema_are_preserved(self):
        """앱을 반복 시작해도 기존 행과 스키마를 보존한다."""
        initialize_database(self.path)
        with closing(connect(self.path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO customer VALUES (?, ?, ?, ?, ?)",
                    (1, "테스트 고객", "test@example.com", "서울", "VIP"),
                )
            before = connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
        for _ in range(2):
            with TestClient(create_app(self.path)) as client:
                self.assertEqual(client.get("/health").status_code, 200)
        with closing(connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM customer").fetchall(),
                             [(1, "테스트 고객", "test@example.com", "서울", "VIP")])
            self.assertEqual(before, connection.execute(
                "SELECT name, sql FROM sqlite_master ORDER BY name").fetchall())

    def test_foreign_keys_and_database_failure(self):
        """SQLAlchemy 연결에서 외래키 검사를 유지하고 DB 장애를 보고한다."""
        app = create_app(self.path)
        with TestClient(app) as client:
            with app.state.engine.connect() as connection:
                self.assertEqual(connection.execute(text("PRAGMA foreign_keys")).scalar(), 1)
                # 테스트 전용 임시 DB에서만 장애 상황을 재현한다.
                connection.execute(text("ALTER TABLE customer RENAME TO unavailable_customer"))
                connection.commit()
            response = client.get("/health")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"], "데이터베이스 연결을 확인해 주세요.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
