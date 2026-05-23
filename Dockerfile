FROM python:3.11-slim

# Install system dependencies for WeasyPrint and Playwright
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-cffi \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy the requirements/project files first for caching
COPY pyproject.toml .

# Install dependencies using pip and hatchling
RUN pip install --no-cache-dir hatchling
RUN pip install --no-cache-dir .

# Install Playwright browsers (with OS dependencies)
RUN playwright install chromium --with-deps

# Copy the rest of the application
COPY . .

# Expose port (Render sets $PORT dynamically)
EXPOSE 8000

CMD ["sh", "-c", "uvicorn cma.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
