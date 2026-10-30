# Gridprint container. Runs on a free Hugging Face Docker Space, or anywhere Docker runs.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# Run as an unprivileged user (Hugging Face Spaces expect user 1000).
RUN useradd -m -u 1000 app
WORKDIR /home/app/gridprint
COPY --chown=app requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app . .
USER app
ENV HOST=0.0.0.0 \
    PORT=7860 \
    PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["python", "server.py"]
