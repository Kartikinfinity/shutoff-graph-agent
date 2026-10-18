# ShutOff web app (FastAPI + static UI). FalkorDB runs as its own service in docker-compose.yml.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shutoff ./shutoff
COPY web ./web

ENV FALKORDB_HOST=falkordb \
    FALKORDB_PORT=6379 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
# Seeds the synthetic network on first start if the graph is missing.
CMD ["uvicorn", "shutoff.api:app", "--host", "0.0.0.0", "--port", "8000"]
