FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --system app && useradd --system --gid app app \
    && mkdir -p /app/temp \
    && chown -R app:app /app

COPY --chown=app:app . .

USER app

CMD ["python", "main.py"]
