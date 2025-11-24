
# Dockerfile for FastAPI app
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for SQL Server ODBC
RUN apt-get update && apt-get install -y \
    curl gnupg ca-certificates \
    build-essential \
    unixodbc unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

# Add Microsoft GPG key and repo for ODBC Driver
RUN set -eux; \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg; \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/microsoft-prod.list

# Install ODBC Driver 18 + sqlcmd tools
RUN apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 mssql-tools && \
    rm -rf /var/lib/apt/lists/*

# Add sqlcmd to PATH
ENV PATH="/opt/mssql-tools/bin:${PATH}"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Expose port 80 for Azure App Service
EXPOSE 80

# Run FastAPI
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "80"]
