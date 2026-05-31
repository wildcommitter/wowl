# WoLW — Wake-on-LAN Web

A small Flask web app to store machines (name + MAC address) and wake them over
the LAN by sending a Wake-on-LAN **magic packet**. Machines are persisted in a
human-editable **YAML** file.

## Features

- Add / list / delete machines from the browser
- One-click **Wake** — broadcasts a magic packet (`FF*6 + MAC*16`)
- Per-machine broadcast address and port (defaults `255.255.255.255:9`)
- **Live "woke up?" status** — tells you whether a machine is online, with the
  IP discovered automatically from its MAC (no manual IP needed)
- YAML storage you can hand-edit (`data/machines.yaml`)
- No authentication — designed for a trusted LAN

## Status / reachability

Each machine shows a live status badge. Determining whether a host is up needs
no root (`CAP_NET_RAW`) and, by default, no configured IP:

1. **MAC → IP discovery.** ARP only maps IP→MAC, so the reverse is found by
   sweeping the local subnet — one throwaway UDP datagram per host makes the
   *kernel* emit ARP requests — then reading `/proc/net/arp` for the entry whose
   MAC matches. The discovered IP is shown in the list (marked *auto*).
2. **ARP reachability** drives the **online/offline** badge: any awake host on
   the subnet answers ARP.
3. **Optional TCP port** (e.g. 22/3389) is probed as a *separate* signal shown
   beside the status (`:22 open` / `:22 closed`). It does **not** affect the
   online badge — that is ARP-only.

Discovery runs **continuously** in a background thread (every 15s) so a host
powered on outside the UI is noticed automatically; the browser refreshes all
rows every 20s, and a freshly **woken** machine is fast-polled every 3s for 30s.

**Requirements & limits:** the target must be on the **same L2 subnet**, and the
container must use **host networking** so it shares the host's ARP table (this is
already required for WoL broadcasts). Subnets larger than a `/22` are not swept —
set an explicit IP for those. If a host has multiple NICs, the WoL MAC must be
the one that answers ARP for its IP.

## Requirements

Wake-on-LAN must be enabled in the target machine's BIOS/UEFI and OS network
adapter settings. The host running this app must be on the same broadcast
domain (same LAN/VLAN) as the machines you want to wake.

## Run locally (python3 venv)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Choose where the YAML store lives (defaults to /data/machines.yaml)
export WOWL_DATA_FILE="$PWD/data/machines.yaml"

# Dev server
python -m app.main
# ...or production-style with gunicorn:
gunicorn --bind 0.0.0.0:8080 app.main:app
```

Open http://localhost:8080.

## Run with Podman / Docker

The magic packet is a UDP **broadcast**, which does not cross the default
bridge network — so run the container with **host networking**.

The image runs as a non-root user. Because `data/` is a bind mount you can
hand-edit, the container must be mapped to your host user so it can write the
YAML file (otherwise you get a `Permission denied` on `machines.yaml`).

There are two build files, one per engine — each tool picks its own with no
`-f` flag needed:

- **`Containerfile`** — used by `podman build .` (preferred over `Dockerfile`)
- **`Dockerfile`** — used by `docker build .`

The two are identical except that the `Containerfile` notes Podman's
HEALTHCHECK/`--format docker` caveat.

**Podman (rootless):**

```bash
podman build -t wowl .          # uses Containerfile

podman run -d --name wolw \
  --network host \
  --userns=keep-id --user "$(id -u):$(id -g)" \
  -v "$PWD/data:/data:Z" \
  ghcr.io/wildcommitter/wowl:latest
```

**Docker:**

```bash
docker build -t wowl .          # uses Dockerfile

docker run -d --name wolw \
  --network host \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/data:/data" \
  ghcr.io/wildcommitter/wowl:latest
```

Or with Compose:

```bash
podman compose up -d      # or: docker compose up -d
```

> Alternatively, use a **named volume** (`-v wolw-data:/data`) instead of a bind
> mount — it works without the user mapping, but the YAML lives inside the
> engine's storage rather than a directory you can easily edit by hand.

## Configuration

| Env var               | Default               | Purpose                                  |
| --------------------- | --------------------- | ---------------------------------------- |
| `WOWL_DATA_FILE`      | `/data/machines.yaml` | Path to the YAML machine store           |
| `WOWL_BIND_ADDRESS`   | `0.0.0.0:8080`        | gunicorn `HOST:PORT` to listen on        |

A health check is exposed at `/healthz`. If you change `WOWL_BIND_ADDRESS` to a
non-default port, update the port in the image's `HEALTHCHECK` accordingly (it
probes `:8080`).

## Publishing images (GitHub Actions)

`.github/workflows/docker-publish.yml` builds a multi-arch image
(`linux/amd64,linux/arm64`) and pushes it to **GHCR** on every semver tag:

```bash
git tag v1.2.3
git push origin v1.2.3
```

This publishes `ghcr.io/wildcommitter/wowl` tagged `1.2.3`, `1.2`, `1`, and `latest`
(pre-release tags like `v1.2.3-rc.1` skip `latest`). The workflow uses the
built-in `GITHUB_TOKEN` — no extra secrets needed. Ensure the repo's
**Settings → Actions → Workflow permissions** allow package writes (or the job's
`packages: write` permission, already set).

## Project layout

```
app/
  main.py        Flask routes (incl. /status, JSON wake)
  wol.py         magic-packet build + send
  status.py      ARP MAC->IP discovery, reachability, periodic sweep
  storage.py     YAML read/write (locked)
  templates/     index.html
  static/        style.css, app.js (wake + live status polling)
gunicorn.conf.py         bind address + starts the discovery sweep
Dockerfile               multi-stage, non-root, venv-based (Docker)
Containerfile            same, Podman variant (used by `podman build`)
docker-compose.yml       host-networking deployment
.github/workflows/       build + publish on semver tag (uses Dockerfile)
```
