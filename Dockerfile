FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN python -m pip install --no-cache-dir -r backend/requirements.txt

COPY backend/__init__.py backend/__init__.py
COPY backend/app/ backend/app/

RUN mkdir -p /app/backend/chroma_db /app/backend/documents

CMD ["sh", "-c", "exec python -m uvicorn backend.app.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1"]
