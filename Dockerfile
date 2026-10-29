FROM python:3.12-slim

WORKDIR /app

# Install system deps for psycopg (binary) and asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

# Default command is the API server; override in compose for worker/notifier
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
