# Podman/Buildah build file. (Docker uses the sibling Dockerfile.)
# `podman build .` prefers this Containerfile automatically — no -f needed.
#
# Note: Podman's default OCI image format ignores the HEALTHCHECK instruction
# and prints a warning. To bake the healthcheck into the image, build with:
#   podman build --format docker -t wowl .
# Otherwise define it at run time: `podman run --health-cmd=... `.

# ---- Builder: install deps into a venv ----
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
# Build the venv that the runtime stage copies verbatim.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---- Runtime ----
FROM python:3.13-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WOLW_DATA_FILE=/data/machines.yaml

# Non-root user; /data is a mountable volume for the YAML store.
RUN useradd --create-home --uid 10001 wolw \
    && mkdir -p /data \
    && chown wolw:wolw /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app/ ./app/

USER wolw
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

# Single worker: the YAML store is guarded by an in-process lock, so multiple
# worker processes could race. One sync worker with threads is plenty here.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "app.main:app"]
