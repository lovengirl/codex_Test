"""홈 화면의 첫 pytest."""


def test_home_returns_200(client):
    """홈 화면이 200 OK와 HTML 본문을 반환한다."""
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Customer Manager" in response.text
