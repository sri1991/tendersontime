FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Which branch to pull — override at build time with --build-arg GIT_BRANCH=...
ARG GIT_BRANCH=phase-1b

RUN git clone --branch ${GIT_BRANCH} --depth 1 \
        https://github.com/sri1991/tendersontime.git .

RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONPATH=/app

# Run as non-root
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
