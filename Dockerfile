FROM python:3.11-slim

# Install Java JRE (required for JPype/MPXJ) and curl
RUN apt-get update && \
    apt-get install -y default-jre curl && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p /app/libs /app/mpp

# Avoid copying large jar files in git. Instead, download MPXJ Uber JAR directly during build
RUN curl -L -o /app/libs/mpxj-all-15.3.1.jar "https://sourceforge.net/projects/mpxj/files/mpxj/15.3.1/mpxj-all-15.3.1.jar/download"

# Copy application files
COPY . .

# Expose port (Render sets PORT env variable)
EXPOSE 8000

# Start server (Uses Render's $PORT environment variable if available, else 8000)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
