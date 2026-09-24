# Multi-platform manifest pinned on 2026-09-24; see docs/deployment.md for updates.
FROM python:3.14.7-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid "${APP_GID}" bot \
    && useradd --uid "${APP_UID}" --gid bot --no-create-home --shell /usr/sbin/nologin bot

WORKDIR /app

COPY src/app/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.txt

COPY --chown=bot:bot src/app/ .
RUN install -d -o bot -g bot /app/database /app/logs

VOLUME ["/app/database", "/app/logs"]
USER bot:bot

CMD ["python", "main.py"]
