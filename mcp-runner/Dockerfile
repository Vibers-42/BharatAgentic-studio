# Runs the MCP tool server in its own container, on a network without the database.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py orders.json ./
EXPOSE 8100
CMD ["python", "server.py"]
