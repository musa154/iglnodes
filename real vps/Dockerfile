# Dockerfile (bot)
FROM python:3.11-slim

WORKDIR /app

# system deps for docker SDK and others
RUN apt-get update && apt-get install -y gcc libffi-dev libssl-dev make git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy app
COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
