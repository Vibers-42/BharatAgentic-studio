# Runs the MCP tool server in its own container, on a network without the database.
FROM python:3.12-slim

WORKDIR /app
# Slow or flaky connections: wait longer for PyPI and retry instead of failing the build.
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py orders.json ./
EXPOSE 8100
CMD ["python", "server.py"]
