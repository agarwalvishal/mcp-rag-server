FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY knowledge_base.py mcp_server.py config.yaml ./
COPY data/ ./data/

ENTRYPOINT ["python", "mcp_server.py"]
