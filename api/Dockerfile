# Builds the FastAPI backend image.
FROM python:3.12-slim

WORKDIR /app
# Slow or flaky connections: wait longer for PyPI and retry instead of failing the build.
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the tokenizer used for "estimated" token counts at build time, so it works offline later.
ENV TIKTOKEN_CACHE_DIR=/app/.tiktoken
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
