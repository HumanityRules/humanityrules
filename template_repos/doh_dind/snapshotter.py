#!/usr/bin/env python3
"""
doh-dind snapshotter: captures the writable layer of Hermes tool containers
to EFS so installed packages survive ECS task restarts.

Runs as PID 1 inside the doh-dind container after /entrypoint.sh restored the
image and started dockerd. Three independent triggers all funnel into the same
do_snapshot() implementation:

  1. `docker events` stream  — on `die` of a hermes-* container, snapshot it;
                               on `start` of one, snapshot+remove older siblings
                               (orphan reap, covers Hermes-only restarts).
  2. Periodic timer          — every 15 min, snapshot any running hermes-*
                               containers whose writable layer has changed.
  3. SIGTERM (from ECS)      — force-snapshot every running hermes-*, then
                               stop dockerd and exit. Covers graceful task
                               shutdown.

Concurrency: one shared lock serializes all do_snapshot() calls so the atomic
rename on latest.tar.zst is uncontested. `docker diff` is a cheap metadata
probe and gates periodic/die-event snapshots; SIGTERM force-snapshots
unconditionally.

Snapshot format: `docker export <id> | zstd`. Flat single-layer tarball; no
image metadata, no overlay layer count to worry about. The restore side in
/entrypoint.sh re-applies the load-bearing ENV vars via `docker import
--change`.

EFS layout (under $PERSISTENCE_DIR):
    latest.tar.zst         # most recent good snapshot
    latest.meta.json       # base_image_ref, ts, fingerprint, container_id
    incoming.tar.zst       # in-flight write; atomic-renamed to latest on success
    snapshots/<ts>.tar.zst # rotated older snapshots (retention=3)
"""

import datetime
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

LOG = logging.getLogger("doh-dind-snapshotter")

PERSISTENCE_DIR = Path(os.environ.get("PERSISTENCE_DIR", "/var/lib/doh-dind/persistence"))
SNAPSHOTS_DIR = PERSISTENCE_DIR / "snapshots"
LATEST = PERSISTENCE_DIR / "latest.tar.zst"
LATEST_META = PERSISTENCE_DIR / "latest.meta.json"
INCOMING = PERSISTENCE_DIR / "incoming.tar.zst"

TOOL_CONTAINER_NAME_PREFIX = "hermes-"  # upstream: tools/environments/docker.py
SNAPSHOT_INTERVAL_SECONDS = 15 * 60
RETENTION = 3

# Passed through from /entrypoint.sh via env so we only configure the daemon
# lifecycle in one place (the shell wrapper that started it).
DOCKERD_PID = int(os.environ.get("DOCKERD_PID", "0"))
TOOLBOX_TAG = os.environ.get("TOOLBOX_TAG", "doh-toolbox:latest")
TOOL_IMAGE_BASE = os.environ.get("TOOL_IMAGE_BASE", "")

_snapshot_lock = threading.Lock()
_shutdown = threading.Event()


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _list_tool_containers(running_only: bool) -> list[dict]:
    """Return [{id, name, created}] for every container whose name starts with hermes-.

    We can't filter by label because upstream doesn't label its tool containers;
    we can't filter by ancestor because after our first snapshot every container
    is an ancestor of doh-toolbox:latest (which we want).
    """
    flags = ["--format", "{{.ID}}\t{{.Names}}\t{{.CreatedAt}}"]
    if not running_only:
        flags.append("-a")
    out = _run(["docker", "ps", *flags]).stdout
    containers = []
    for line in out.strip().splitlines():
        cid, name, created = line.split("\t", 2)
        if name.startswith(TOOL_CONTAINER_NAME_PREFIX):
            containers.append({"id": cid, "name": name, "created": created})
    return containers


def _diff_fingerprint(container_id: str) -> str:
    """Hash of `docker diff` output. Changes when the writable layer changes."""
    try:
        out = _run(["docker", "diff", container_id]).stdout
    except subprocess.CalledProcessError:
        # Container gone between listing and diff — caller will handle.
        return ""
    return hashlib.sha256(out.encode()).hexdigest()


def _load_latest_meta() -> dict:
    if not LATEST_META.exists():
        return {}
    try:
        return json.loads(LATEST_META.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _prune_snapshots() -> None:
    snaps = sorted(SNAPSHOTS_DIR.glob("*.tar.zst"))
    for old in snaps[:-RETENTION]:
        try:
            old.unlink()
        except OSError as e:
            LOG.error("prune: failed to remove %s: %s", old, e)


def do_snapshot(container_id: str, trigger: str, force: bool) -> bool:
    """Snapshot a single tool container. Returns True if a new tarball was written.

    The docker-diff fingerprint guard makes periodic snapshots effectively
    free when the container is idle (the common case between bursts of agent
    activity). SIGTERM callers pass force=True to override the guard — we'd
    rather write an identical tarball than miss a change we already saw.
    """
    with _snapshot_lock:
        started = time.monotonic()
        fingerprint = _diff_fingerprint(container_id)

        meta = _load_latest_meta()
        if not force and fingerprint and meta.get("fingerprint") == fingerprint:
            LOG.info(
                "snapshot skipped (unchanged) container=%s trigger=%s",
                container_id[:12], trigger,
            )
            return False

        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        # docker export streams the writable layer; pipe into zstd to shrink
        # before landing on EFS. shell=True lets us build the pipeline in one
        # subprocess group — if either side fails the write stops cleanly.
        if INCOMING.exists():
            INCOMING.unlink()
        cmd = f"docker export {container_id} | zstd -T0 -q -o {INCOMING}"
        try:
            subprocess.run(cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            LOG.error(
                "snapshot failed container=%s trigger=%s err=%s",
                container_id[:12], trigger, e,
            )
            if INCOMING.exists():
                INCOMING.unlink()
            return False

        size_bytes = INCOMING.stat().st_size

        # Rotate current latest into snapshots/ BEFORE renaming incoming into
        # latest, so readers that hit a torn state still see a prior good copy.
        ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        if LATEST.exists():
            rotated = SNAPSHOTS_DIR / f"{ts}.tar.zst"
            try:
                os.replace(LATEST, rotated)
            except OSError as e:
                LOG.error("rotate: failed to move latest to %s: %s", rotated, e)

        os.replace(INCOMING, LATEST)

        new_meta = {
            "fingerprint": fingerprint,
            "container_id": container_id,
            "base_image_ref": TOOL_IMAGE_BASE,
            "created_utc": ts,
            "size_bytes": size_bytes,
            "trigger": trigger,
        }
        LATEST_META.write_text(json.dumps(new_meta, indent=2))
        _prune_snapshots()

        duration_ms = int((time.monotonic() - started) * 1000)
        LOG.info(
            "snapshot written container=%s trigger=%s size=%d ms=%d",
            container_id[:12], trigger, size_bytes, duration_ms,
        )
        return True


def _reap_older_siblings(newest: dict) -> None:
    """Snapshot + remove any running hermes-* container older than `newest`.

    Triggered on `docker events start` for a new tool container. The goal is
    that once Hermes restarts (as a sibling to DinD) and spins up a fresh
    tool container, whatever state the previous one accumulated lands on EFS
    exactly once before the old container is deleted.
    """
    siblings = [c for c in _list_tool_containers(running_only=True) if c["id"] != newest["id"]]
    for c in siblings:
        LOG.info("reaping orphan tool container %s (%s)", c["id"][:12], c["name"])
        do_snapshot(container_id=c["id"], trigger="orphan-reap", force=True)
        try:
            _run(["docker", "rm", "-f", c["id"]])
        except subprocess.CalledProcessError as e:
            LOG.error("rm -f failed for orphan %s: %s", c["id"][:12], e)


def events_stream() -> None:
    """Long-poll `docker events` and dispatch start/die to snapshot/reap."""
    # event=create is NOT enough — we need the container to be findable in
    # `docker ps`, which happens on start. die fires before rm, so we can
    # still export the stopped-but-present filesystem.
    proc = subprocess.Popen(
        [
            "docker", "events", "--format", "{{json .}}",
            "--filter", "type=container",
            "--filter", "event=start",
            "--filter", "event=die",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line or _shutdown.is_set():
                break
            try:
                evt = json.loads(line)
                name = evt.get("Actor", {}).get("Attributes", {}).get("name", "")
                if not name.startswith(TOOL_CONTAINER_NAME_PREFIX):
                    continue
                status = evt.get("status")
                cid = evt.get("id", "")
                if status == "start":
                    _reap_older_siblings({"id": cid, "name": name})
                elif status == "die":
                    do_snapshot(container_id=cid, trigger="die-event", force=False)
            except Exception as e:
                # Per-event guard: a single malformed event or failed reap
                # must not kill the stream. The timer loop is a fallback for
                # anything we miss here, but losing the stream entirely would
                # hide orphan accumulation.
                LOG.exception("event handling failed: %s", e)
    finally:
        proc.terminate()


def timer_loop() -> None:
    """Periodic fallback snapshot for long-running containers the event path
    never catches (a container that never dies between task shutdowns).
    Guarded by docker-diff so idle containers cost nothing."""
    while not _shutdown.wait(SNAPSHOT_INTERVAL_SECONDS):
        try:
            for c in _list_tool_containers(running_only=True):
                do_snapshot(container_id=c["id"], trigger="periodic", force=False)
        except Exception as e:
            LOG.exception("timer_loop iteration failed: %s", e)


def _force_snapshot_all(trigger: str) -> None:
    try:
        containers = _list_tool_containers(running_only=True)
    except subprocess.CalledProcessError as e:
        LOG.error("could not list tool containers during %s: %s", trigger, e)
        return
    # Parallelism here is overkill (these are serialized by the lock anyway)
    # and adds signal-handling complexity, so serialize.
    for c in containers:
        do_snapshot(container_id=c["id"], trigger=trigger, force=True)


def _stop_dockerd() -> None:
    """Best-effort graceful dockerd shutdown. Called during SIGTERM after
    snapshotting. We wait up to 30s for dockerd to exit before letting the
    process tree die — ECS will SIGKILL us after stopTimeout (120s) regardless."""
    if DOCKERD_PID <= 0:
        return
    try:
        os.kill(DOCKERD_PID, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.kill(DOCKERD_PID, 0)
        except ProcessLookupError:
            return
        time.sleep(0.5)
    LOG.error("dockerd (pid=%d) did not exit within 30s after SIGTERM", DOCKERD_PID)


def _handle_sigterm(signum, frame) -> None:
    LOG.info("received signal %d; starting shutdown sequence", signum)
    _shutdown.set()
    _force_snapshot_all(trigger="sigterm")
    _stop_dockerd()
    sys.exit(0)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    PERSISTENCE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    # Background threads: events + periodic timer. main() then waits on the
    # shutdown event so SIGTERM can be delivered to the main thread (Python
    # only delivers signals there).
    threading.Thread(target=events_stream, name="events", daemon=True).start()
    threading.Thread(target=timer_loop, name="timer", daemon=True).start()

    LOG.info(
        "doh-dind snapshotter started (persistence=%s, interval=%ds, dockerd_pid=%d)",
        PERSISTENCE_DIR, SNAPSHOT_INTERVAL_SECONDS, DOCKERD_PID,
    )
    # Park the main thread so signals land here. The daemon threads exit with
    # the process; we only leave via _handle_sigterm -> sys.exit.
    while not _shutdown.is_set():
        _shutdown.wait(60)


if __name__ == "__main__":
    main()
