# ================================================
# Stage 1: Build Python wheels
# ================================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Install compilation tools if needed by any python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install dependencies to user directory for easy copying
RUN pip install --no-cache-dir --user -r requirements.txt

# ================================================
# Stage 2: Runtime Environment
# ================================================
# Use official Playwright Python image containing pre-configured Chromium and dependencies
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Copy installed Python packages from builder stage
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY src/ /app/src/

# Expose port (Cloud Run dynamically sets PORT env variable at runtime)
EXPOSE 8080

# Set runtime defaults
ENV PORT=8080
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

# Run the FastAPI server
CMD uvicorn src.main:app --host $HOST --port $PORT
