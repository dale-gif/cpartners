FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN mkdir -p /app/fonts && \
    curl -fsSL -o /tmp/inter.zip "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip" && \
    unzip -q /tmp/inter.zip -d /tmp/inter && \
    cp /tmp/inter/extras/ttf/Inter-Regular.ttf /app/fonts/ && \
    cp /tmp/inter/extras/ttf/Inter-Black.ttf /app/fonts/ && \
    cp /tmp/inter/extras/ttf/Inter-ExtraBold.ttf /app/fonts/ && \
    rm -rf /tmp/inter /tmp/inter.zip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV WORK_ROOT=/app/work
ENV FONT_DIR=/app/fonts
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
