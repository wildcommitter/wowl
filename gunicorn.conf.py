"""Gunicorn configuration.

The bind address is configurable via the WOWL_BIND_ADDRESS environment
variable (format: HOST:PORT). Defaults to 0.0.0.0:8080.
"""
import os

bind = os.environ.get("WOWL_BIND_ADDRESS", "0.0.0.0:8080")

# Single worker: the YAML store is guarded by an in-process lock, so multiple
# worker processes could race. One sync worker with threads is plenty here.
workers = 1
threads = 4


def post_fork(server, worker):
    """Start the periodic IP-discovery sweep inside the worker process."""
    from app import status, storage

    status.start_discovery(lambda: [m["mac"] for m in storage.list_machines()])
