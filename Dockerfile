FROM python:3.11-slim

# Install system dependencies including FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    curl \
    git \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for local storage
RUN mkdir -p /app/data /app/downloads /app/temp

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Expose port
EXPOSE 10000

# Start command - runs both Flask (health check) and bot via gunicorn + threading
CMD ["python", "main.py"]
