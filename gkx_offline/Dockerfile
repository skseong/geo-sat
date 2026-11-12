FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py pdf_extract.py server.py ./
COPY static ./static
ENV PYTHONUNBUFFERED=1 GKX_HOST=0.0.0.0 GKX_AI_PROVIDER=openai PORT=8765
EXPOSE 8765
CMD ["python", "server.py"]
