# Use official Python image
FROM python:3.13-slim

# Install system dependencies
COPY packages.txt /tmp/packages.txt
RUN apt-get update && apt-get install -y \
    $(cat /tmp/packages.txt) \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your app code
COPY . .

# Expose port 8080 for Render
EXPOSE 8080

# Command to run the app
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]
