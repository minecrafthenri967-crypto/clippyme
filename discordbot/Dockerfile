# Discord gatekeeper bot. Deliberately tiny and separate from the backend
# image: it needs no ffmpeg, no CV stack, and no model weights — just an HTTP
# client and discord.py — so it builds in seconds and restarts instantly.
FROM python:3.11-slim

WORKDIR /app

# Copied first so a bot.py edit does not invalidate the dependency layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Unbuffered so `docker compose logs -f discordbot` shows output as it happens
# rather than in delayed blocks.
ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
