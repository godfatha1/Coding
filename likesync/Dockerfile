# Minimal image: no runtime dependencies to install.
FROM python:3.12-slim

# State (SQLite + tokens) lives on a volume so it survives container replacement.
ENV LIKESYNC_HOME=/data \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 likesync \
    && mkdir -p /data && chown likesync:likesync /data

USER likesync
VOLUME ["/data"]

ENTRYPOINT ["likesync"]
CMD ["sync"]
