FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY db ./db
COPY eval ./eval

RUN pip install --no-cache-dir -e ".[api]"

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "taksitlio.main:app", "--host", "0.0.0.0", "--port", "8000"]
