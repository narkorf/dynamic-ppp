FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    GEOIP_DB_PATH=/app/dynamic_ppp_api/data/ip-to-country.mmdb \
    PPP_DATA_PATH=/app/dynamic_ppp_api/data/ppp_snapshot.json

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

COPY pyproject.toml README.md ./
COPY dynamic_ppp_api ./dynamic_ppp_api

RUN python -m pip install --upgrade pip && \
    python -m pip install .

RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "dynamic_ppp_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
