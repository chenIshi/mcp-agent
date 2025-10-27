# Minimal Dockerfile for packaging your MCP app + mcp-agent
FROM python:3.11-slim

WORKDIR /app

# Install system deps (add as needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

# Copy pyproject/requirements if you use them, or install mcp-agent directly
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code
COPY . /app

# Expose port(s) if needed by your app
EXPOSE 8000

# Run your app (adjust entrypoint/cmd as required)
CMD ["uv", "run", "main.py"]