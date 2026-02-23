FROM python:3.11-slim

# Don't buffer stdout/stderr — important for Docker log visibility
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system deps needed by cryptography (for PyJWT RS256)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000 8001

# Default: run the FastAPI webhook server.
# Override CMD in docker-compose to run the MCP server instead.
CMD ["python", "-m", "app.mcp_server"]