FROM python:3.12.7-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY midplane ./midplane
COPY migrations ./migrations

RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000

CMD ["python", "-m", "midplane.gateway"]
