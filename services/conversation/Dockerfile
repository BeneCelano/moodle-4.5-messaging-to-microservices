# Image of the ConversationService. Two-stage build: the final image keeps only what runs the
# service.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Dependencies come from the lock file, generated from the running image:
#
#     kubectl -n messaging exec deploy/conversation-service -- pip freeze > requirements.lock
COPY requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.lock

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The service does not run as root.
RUN useradd --create-home --uid 10001 conversation

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
# One-off operations run as Jobs on the cluster and reuse this image.
COPY scripts ./scripts

# Copied files keep the permissions of the build machine: made readable explicitly.
RUN chmod -R a+rX /app

USER conversation

EXPOSE 8080

# One worker per container; horizontal scale by the number of replicas.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
