FROM python:3.12-slim

WORKDIR /app

# Set environment variables for Python behavior
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies if any are needed (e.g. for building packages)
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure the instance directory exists for the SQLite database
RUN mkdir -p /app/instance && chown -R 1000:1000 /app/instance

# Switch to non-root user
USER 1000

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request, sys; \
         r = urllib.request.urlopen('http://localhost:5001/healthz', timeout=5); \
         sys.exit(0 if r.status == 200 else 1)"

CMD ["python", "app.py"]
