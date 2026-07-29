# Discord gatekeeper bot. Deliberately tiny and separate from the backend
# image: no CV stack, no model weights — just an HTTP client, discord.py,
# and ffmpeg (only for shrinking the composed preview to fit Discord's
# upload limit; the backend does every real render/compose pass).
FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copied first so a bot.py edit does not invalidate the dependency layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Unbuffered so `docker compose logs -f discordbot` shows output as it happens
# rather than in delayed blocks.
ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
