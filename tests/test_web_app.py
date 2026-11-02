import base64
import subprocess
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from web_app import app


def _auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    return {"Authorization": f"Basic {token}"}


FIXTURE_ROOT = Path(__file__).parent
TEST_USERNAME = "test-user"
TEST_PASSWORD = "test-password"
AUTH_HEADERS = _auth_headers(TEST_USERNAME, TEST_PASSWORD)


@pytest.fixture(autouse=True)
def configure_test_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_USERNAME", TEST_USERNAME)
    monkeypatch.setenv("AUTH_PASSWORD", TEST_PASSWORD)


@pytest.mark.parametrize(
    ("fixture_name", "setters"),
    [
        ("u18-tiebreak", ("1, 4", "")),
        ("u14-tiebreak", ("", ""))
    ]
)
def test_upload_returns_analysis_report_pdf(
    fixture_name: str,
    setters: tuple[str, str]
) -> None:
    pdf_path = FIXTURE_ROOT / fixture_name / "match.pdf"
    response = _request(
        "POST",
        "/process",
        data={
            "team_a_setters": setters[0],
            "team_b_setters": setters[1]
        },
        headers=AUTH_HEADERS,
        files={
            "pdf_file": (
                "match.pdf",
                pdf_path.read_bytes(),
                "application/pdf"
            )
        }
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="spieltaganalyse.pdf"'
    )
    assert response.content.startswith(b"%PDF-")
    report_text = _pdf_text(response.content)
    if fixture_name == "u18-tiebreak":
        assert "VSG TV Bad Windsheim" in report_text
        assert "13" in report_text
    else:
        assert "Post SV Nürnberg" in report_text


def test_invalid_pdf_returns_readable_error_pdf() -> None:
    response = _request(
        "POST",
        "/process",
        data={"team_a_setters": "", "team_b_setters": ""},
        headers=AUTH_HEADERS,
        files={"pdf_file": ("match.pdf", b"not a PDF", "application/pdf")}
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="spieltaganalyse-fehler.pdf"'
    )
    error_text = _pdf_text(response.content)
    assert "Analyse nicht möglich" in error_text
    assert "kein lesbares PDF" in error_text


def test_missing_pdf_returns_readable_error_pdf() -> None:
    response = _request(
        "POST",
        "/process",
        data={"team_a_setters": "", "team_b_setters": ""},
        headers=AUTH_HEADERS
    )

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/pdf"
    assert "Spielbericht-PDF hoch" in _pdf_text(response.content)


def test_invalid_setter_input_returns_readable_error_pdf() -> None:
    pdf_path = FIXTURE_ROOT / "u18" / "match.pdf"
    response = _request(
        "POST",
        "/process",
        data={"team_a_setters": "four", "team_b_setters": ""},
        headers=AUTH_HEADERS,
        files={
            "pdf_file": (
                "match.pdf",
                pdf_path.read_bytes(),
                "application/pdf"
            )
        }
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/pdf"
    error_text = _pdf_text(response.content)
    assert "durch Kommas getrennte positive Zahlen" in error_text


def test_health_check() -> None:
    response = _request("GET", "/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_page_requires_authentication() -> None:
    response = _request("GET", "/")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        'Basic realm="Spieltaganalyse"'
    )


def test_process_endpoint_requires_authentication() -> None:
    response = _request("POST", "/process")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        'Basic realm="Spieltaganalyse"'
    )


def test_upload_rejects_invalid_credentials() -> None:
    response = _request(
        "POST",
        "/process",
        data={"team_a_setters": "", "team_b_setters": ""},
        headers=_auth_headers("intruder", "wrong-password"),
        files={"pdf_file": ("match.pdf", b"not a PDF", "application/pdf")}
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        'Basic realm="Spieltaganalyse"'
    )


def test_service_fails_closed_when_credentials_are_unset(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AUTH_USERNAME")

    response = _request("GET", "/", headers=AUTH_HEADERS)

    assert response.status_code == 503


def test_upload_page_has_form_fields() -> None:
    response = _request("GET", "/", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="pdf_file"' in response.text
    assert 'name="team_a_setters"' in response.text
    assert 'name="team_b_setters"' in response.text


def _pdf_text(pdf_content: bytes) -> str:
    result = subprocess.run(
        ["pdftotext", "-", "-"],
        input=pdf_content,
        check=True,
        capture_output=True
    )
    return result.stdout.decode("utf-8")


def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)

    return anyio.run(send_request)
