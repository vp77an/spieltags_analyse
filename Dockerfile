FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY main.py match_reconstructor.py match_rules.py pdf_score_sheet.py \
     rotation_analysis.py web_app.py ./
COPY web ./web

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /tmp/matplotlib \
    && chown -R appuser:appuser /app /tmp/matplotlib

USER 10001

EXPOSE 8080
CMD ["python", "web_app.py"]
