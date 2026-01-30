FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/
# Copy local data folder (optional, can be mounted instead)
# COPY data/ data/ 

# Set python path
ENV PYTHONPATH=/app

# Default command (can be overridden in docker-compose)
CMD ["python", "src/api.py"]
