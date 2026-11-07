"""Publishing each stop's JSON payload to a static web host, for the boards to fetch.

The Pi writes the files into data/publish/v1/ and pushes that directory with rsync over SSH.
The connection is outbound only, so nothing is exposed on the home network, and the host only
needs to serve static files. If the Pi or the home internet goes down, the files stop updating
and the boards see an old "now".

Layout on the host (under the target directory, which must end in /v1):
  v1/index.json          {v, now, stops: {code: name}}
  v1/stops/<code>.json   the payload (see payload.py)
  v1/.htaccess           no caching, JSON content type, no directory listing
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path

import httpx

from .static_gtfs import USER_AGENT

log = logging.getLogger(__name__)

HTACCESS_TEMPLATE = Path(__file__).resolve().parent.parent / "deploy" / "dreamhost" / "htaccess"
RSYNC_TIMEOUT_S = 60


class Publisher:
    def __init__(self, target: str, public_url: str, ssh_key: Path, data_dir: Path):
        self.target = target
        self.public_url = public_url.rstrip("/")
        self.ssh_key = Path(ssh_key).expanduser()
        self.staging = data_dir / "publish" / "v1"
        self.control_dir = data_dir / "publish"
        self.last_ok: float | None = None
        self.last_error: str | None = None
        self.last_push_ms: int | None = None
        self.pushes = 0
        self.last_verify: float | None = None
        self.served_lag_s: int | None = None
        self.verify_error: str | None = None

    # ---- local files
    def write(self, payloads: dict[str, dict], now: int) -> None:
        """Write every stop's payload, the index and .htaccess into the staging directory."""
        stops_dir = self.staging / "stops"
        stops_dir.mkdir(parents=True, exist_ok=True)
        for code, payload in payloads.items():
            _write_atomic(stops_dir / f"{code}.json", json.dumps(payload, separators=(",", ":")).encode())
        index = {"v": 1, "now": now, "stops": {code: p.get("name", code) for code, p in payloads.items()}}
        _write_atomic(self.staging / "index.json", json.dumps(index, separators=(",", ":")).encode())
        _write_atomic(self.staging / ".htaccess", HTACCESS_TEMPLATE.read_bytes())

    # ---- upload
    def _check_target(self) -> None:
        path = self.target.split(":", 1)[1] if _is_remote(self.target) else self.target
        if path.rstrip("/").split("/")[-1] != "v1":
            raise ValueError(f"publish target must end in /v1 (got {self.target!r})")

    def _ssh_command(self) -> str:
        opts = [
            "ssh", "-i", str(self.ssh_key),
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
            # Reuse one connection across pushes instead of a new SSH handshake every 30 s
            "-o", "ControlMaster=auto", "-o", f"ControlPath={self.control_dir}/ssh-%C", "-o", "ControlPersist=120",
            "-o", "ServerAliveInterval=30",
        ]
        return shlex.join(opts)

    def push(self) -> bool:
        """rsync the staging directory to the target. Never raises; returns success."""
        start = time.monotonic()
        try:
            self._check_target()
            # --checksum: decide by content, not size+mtime (two updates in the same second with the same
            # size would otherwise be skipped); cheap for a dozen ~1 KB files
            cmd = ["rsync", "-rtz", "--checksum", "--chmod=D755,F644", "--delay-updates", "--timeout=20",
                   "--exclude=.*.tmp"]
            if _is_remote(self.target):
                cmd += ["-e", self._ssh_command()]
            cmd += [f"{self.staging}/", self.target.rstrip("/") + "/"]
            # No --delete: a mistyped target must never be able to remove other files
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT_S)
            if res.returncode != 0:
                raise RuntimeError(f"rsync exit {res.returncode}: {(res.stderr or res.stdout).strip()[-300:]}")
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("publish failed: %s", self.last_error)
            return False
        self.last_push_ms = int((time.monotonic() - start) * 1000)
        self.last_ok = time.time()
        self.last_error = None
        self.pushes += 1
        return True

    # ---- check what the boards would get
    def verify(self) -> int | None:
        """Fetch the published index.json and return how old its "now" is (s), or None on failure."""
        self.last_verify = time.time()
        try:
            resp = httpx.get(f"{self.public_url}/index.json", timeout=15, headers={"User-Agent": USER_AGENT},
                             follow_redirects=True)
            resp.raise_for_status()
            self.served_lag_s = int(time.time() - resp.json()["now"])
            self.verify_error = None
        except Exception as e:
            self.served_lag_s = None
            self.verify_error = f"{type(e).__name__}: {e}"
        return self.served_lag_s

    def status(self) -> dict:
        now = time.time()
        return {
            "target": self.target,
            "public_url": self.public_url,
            "last_ok_age_s": round(now - self.last_ok) if self.last_ok else None,
            "last_push_ms": self.last_push_ms,
            "pushes": self.pushes,
            "error": self.last_error,
            "served_lag_s": self.served_lag_s,
            "verify_age_s": round(now - self.last_verify) if self.last_verify else None,
            "verify_error": self.verify_error,
        }


def _is_remote(target: str) -> bool:
    # rsync's rule: "host:path" is remote unless the colon comes after a slash
    head, sep, _ = target.partition(":")
    return bool(sep) and "/" not in head


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
