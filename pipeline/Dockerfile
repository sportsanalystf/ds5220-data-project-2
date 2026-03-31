FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached on rebuilds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fetch.py .

CMD ["python", "fetch.py"]
