import io
import json
import logging
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from match_reconstructor import reconstruct_match
from pdf_score_sheet import PdfScoreSheetParser
from rotation_analysis import analyze_rotation_runs


LOGGER = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 15 * 1024 * 1024))
UPLOAD_CHUNK_BYTES = 1024 * 1024
WEB_ROOT = Path(__file__).with_name("web")
ERROR_FONT_NAME = "MatchAnalysisDejaVuSans"
ERROR_FONT_PATH = Path(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
)
HTTP_BASIC = HTTPBasic(auto_error=False)

app = FastAPI(
    title="Spieltaganalyse",
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)


def _require_basic_auth(
    credentials: HTTPBasicCredentials | None = Depends(HTTP_BASIC)
) -> None:
    username = os.environ.get("AUTH_USERNAME")
    password = os.environ.get("AUTH_PASSWORD")
    if not username or not password:
        LOGGER.error("Web authentication is not configured")
        raise HTTPException(
            status_code=503,
            detail="The analysis service is not configured"
        )
    if credentials is None:
        _raise_authentication_required()

    username_matches = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        username.encode("utf-8")
    )
    password_matches = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        password.encode("utf-8")
    )
    if not (username_matches and password_matches):
        _raise_authentication_required()


def _raise_authentication_required() -> None:
    raise HTTPException(
        status_code=401,
        detail="Authentication required",
        headers={"WWW-Authenticate": 'Basic realm="Spieltaganalyse"'}
    )


@app.get("/")
def index(_: None = Depends(_require_basic_auth)) -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html", media_type="text/html")


@app.get("/healthz")
def health_check() -> dict[str, str]:
    if not os.environ.get("AUTH_USERNAME") or not os.environ.get("AUTH_PASSWORD"):
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured"
        )
    return {"status": "ok"}


@app.post("/process")
def process_pdf(
    pdf_file: Annotated[UploadFile | None, File()] = None,
    team_a_setters: Annotated[str, Form()] = "",
    team_b_setters: Annotated[str, Form()] = "",
    _: None = Depends(_require_basic_auth)
) -> Response:
    if pdf_file is None:
        return _error_response(
            "Bitte laden Sie ein Spielbericht-PDF hoch.",
            status_code=400
        )

    try:
        setters_by_header_order = [
            _parse_setters(team_a_setters),
            _parse_setters(team_b_setters)
        ]
    except ValueError as error:
        pdf_file.file.close()
        return _error_response(str(error), status_code=422)

    try:
        with tempfile.TemporaryDirectory(prefix="spieltaganalyse-") as temp_dir:
            work_dir = Path(temp_dir)
            pdf_path = work_dir / "uploaded-match.pdf"
            prefix = bytearray()
            byte_count = 0
            with pdf_path.open("wb") as saved_pdf:
                while chunk := pdf_file.file.read(UPLOAD_CHUNK_BYTES):
                    byte_count += len(chunk)
                    if byte_count > MAX_UPLOAD_BYTES:
                        return _error_response(
                            "Die PDF-Datei ist zu gross. Bitte laden Sie eine "
                            f"Datei mit höchstens "
                            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB hoch.",
                            status_code=413
                        )
                    if len(prefix) < 1024:
                        prefix.extend(chunk[:1024 - len(prefix)])
                    saved_pdf.write(chunk)

            if byte_count == 0:
                return _error_response(
                    "Die hochgeladene Datei ist leer.",
                    status_code=422
                )
            if b"%PDF-" not in prefix:
                return _error_response(
                    "Die Datei ist kein lesbares PDF. Bitte wählen Sie den "
                    "Spielbericht als PDF-Datei aus.",
                    status_code=422
                )

            parser = PdfScoreSheetParser()
            page = parser.extract_page(pdf_path)
            team_order = parser.extract_team_header_order(pdf_path)
            if len(team_order) != 2:
                return _error_response(
                    "Im PDF konnten nicht genau zwei Teams erkannt werden.",
                    status_code=422
                )

            parsed_match = parser.parse_match(page)
            match_data = json.loads(parsed_match.to_json())
            point_data = reconstruct_match(match_data)
            teams = [
                {"name": team_name, "setters": setters}
                for team_name, setters in zip(
                    team_order,
                    setters_by_header_order,
                    strict=True
                )
            ]
            analyze_rotation_runs(point_data, teams, work_dir)

            reports = list(work_dir.glob("report_*.pdf"))
            if len(reports) != 1:
                raise RuntimeError(
                    "Expected the analysis to create exactly one PDF report"
                )
            report_bytes = reports[0].read_bytes()

        return _pdf_response(
            report_bytes,
            "spieltaganalyse.pdf",
            status_code=200
        )
    except ValueError as error:
        LOGGER.info("Match analysis rejected an uploaded PDF")
        return _error_response(str(error), status_code=422)
    except subprocess.TimeoutExpired:
        return _error_response(
            "Das PDF konnte innerhalb der verfügbaren Zeit nicht gelesen "
            "werden. Bitte prüfen Sie, ob es sich um einen gültigen, "
            "ungeschützten Spielbericht handelt.",
            status_code=422
        )
    except subprocess.CalledProcessError:
        return _error_response(
            "Das PDF konnte nicht gelesen werden. Bitte verwenden Sie einen "
            "gültigen, ungeschützten Spielbericht im PDF-Format.",
            status_code=422
        )
    except FileNotFoundError:
        LOGGER.exception("A required file or PDF utility is missing")
        return _error_response(
            "Der Analysedienst ist momentan nicht vollständig eingerichtet. "
            "Bitte versuchen Sie es später erneut.",
            status_code=500
        )
    except Exception:
        LOGGER.exception("Unexpected error while processing an uploaded match")
        return _error_response(
            "Bei der Analyse ist ein unerwarteter Fehler aufgetreten. Bitte "
            "versuchen Sie es erneut. Wenn der Fehler wieder auftritt, "
            "wenden Sie sich bitte an den Website-Betreiber.",
            status_code=500
        )
    finally:
        pdf_file.file.close()


def _parse_setters(value: str) -> list[int]:
    if not value.strip():
        return []
    if len(value) > 120:
        raise ValueError(
            "Bitte geben Sie nur die Setter-Trikotnummern an, durch Kommas "
            "getrennt, zum Beispiel 4, 12."
        )

    setters = []
    for item in value.split(","):
        jersey_text = item.strip()
        if not jersey_text.isdecimal():
            raise ValueError(
                "Bitte geben Sie die Trikotnummern als durch Kommas "
                "getrennte positive Zahlen ein, zum Beispiel 4, 12. Das Feld "
                "kann leer bleiben, wenn die Setter unbekannt sind."
            )
        try:
            jersey = int(jersey_text)
        except ValueError as error:
            raise ValueError(
                "Bitte geben Sie die Trikotnummern als durch Kommas "
                "getrennte positive Zahlen ein, zum Beispiel 4, 12. Das Feld "
                "kann leer bleiben, wenn die Setter unbekannt sind."
            ) from error
        if jersey < 1:
            raise ValueError(
                "Trikotnummern müssen positive Zahlen sein. Trennen Sie "
                "mehrere Nummern mit Kommas, zum Beispiel 4, 12."
            )
        if jersey in setters:
            raise ValueError(
                f"Die Trikotnummer {jersey} ist mehrfach eingetragen. Bitte "
                "geben Sie jede Setter-Nummer nur einmal an."
            )
        setters.append(jersey)
    return setters


def _pdf_response(
    content: bytes,
    filename: str,
    status_code: int
) -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        status_code=status_code,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"
        }
    )


def _error_response(message: str, status_code: int) -> Response:
    return _pdf_response(
        _create_error_pdf(message),
        "spieltaganalyse-fehler.pdf",
        status_code
    )


def _create_error_pdf(message: str) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    page_width, page_height = A4
    font_name = "Helvetica"
    if ERROR_FONT_PATH.is_file():
        if ERROR_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(
                TTFont(ERROR_FONT_NAME, str(ERROR_FONT_PATH))
            )
        font_name = ERROR_FONT_NAME

    pdf.setTitle("Fehler bei der Spieltaganalyse")
    pdf.setFillColor(colors.HexColor("#15345b"))
    pdf.setFont(font_name, 18)
    pdf.drawString(48, page_height - 64, "Analyse nicht möglich")

    pdf.setFillColor(colors.HexColor("#202124"))
    pdf.setFont(font_name, 11)
    text_width = page_width - 96
    lines = []
    for paragraph in message[:3000].splitlines():
        lines.extend(simpleSplit(paragraph or " ", font_name, 11, text_width))

    y = page_height - 100
    for line in lines:
        if y < 54:
            pdf.showPage()
            pdf.setFillColor(colors.HexColor("#202124"))
            pdf.setFont(font_name, 11)
            y = page_height - 54
        pdf.drawString(48, y, line)
        y -= 18

    pdf.save()
    return buffer.getvalue()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080"))
    )
