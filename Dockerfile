FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ ./src/
COPY main.py .

RUN useradd -m -u 1000 appuser
USER appuser

EXPOSE 8888 9090

ENV PROXY_PORT=8888
ENV METRICS_PORT=9090
ENV PYTHONPATH=/app/src

CMD ["python", "main.py"]
