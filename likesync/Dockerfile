# Includes Chromium, because SoundCloud web mode drives a real browser.
# For "api" mode only, python:3.12-slim with `pip install .` is enough and the
# image has no runtime dependencies at all.
FROM python:3.12-slim

# State (SQLite, tokens, browser profile) lives on a volume so it survives
# container replacement. Losing it costs you a re-login, not just a re-sync.
ENV LIKESYNC_HOME=/data \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir '.[web,crypt]' \
    # --with-deps pulls the shared libraries headless Chromium needs.
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 likesync \
    && mkdir -p /data && chown likesync:likesync /data \
    && chmod -R a+rX /opt/pw-browsers

USER likesync
VOLUME ["/data"]

ENTRYPOINT ["likesync"]
CMD ["sync"]
