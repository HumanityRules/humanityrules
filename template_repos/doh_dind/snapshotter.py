#!/usr/bin/env python3
"""
doh-dind snapshotter: keeps doh-toolbox:latest carrying the accumulated
state of Hermes tool containers across container deaths, Hermes restarts,
and ECS task replacements.

Runs as PID 1 inside the doh-dind container after /entrypoint.sh staged
doh-toolbox:latest and started dockerd. Two operations feed three triggers:

  do_commit(container_id)       — in-daemon `docker commit` onto
                                  doh-toolbox:latest. Fast (metadata only),
                                  no I/O. Every new container Hermes spawns
                                  from doh-toolbox:latest immediately sees
                                  the prior container's packages.

  do_save()                     — flatten doh-toolbox:latest to one layer
                                  via `docker export | docker import` so
                                  the commit-layer chain can't exceed
                                  overlay2's 127-layer cap, then stream
                                  `docker save | zstd` to EFS for cross-
                                  ECS-task-restart durability. Slow (I/O).

Triggers:

  1. `docker events die`        — commit only. Fast path for Hermes-only
                                  restarts and per-conversation container
                                  recycling.
  2. `docker events start`      — orphan reap. Commit the *older* siblings
                                  then remove them; leave the newly-started
                                  container alone (it's Hermes's live one).
  3. 15-min timer               — commit + save. Bounds cross-task-restart
                                  data loss to at most 15 min. Guarded by
                                  `docker diff` fingerprint so idle
                                  containers skip both ops.
  4. SIGTERM                    — commit + save for every live tool
                                  container, then stop dockerd. Graceful
                                  ECS task shutdown.

EFS layout (under $PERSISTENCE_DIR):

    latest.tar.zst        # `docker save` of flattened doh-toolbox:latest
    latest.meta.json      # base_image_ref, ts, container_id, trigger
    incoming.tar.zst      # in-flight write; atomic-renamed to latest
    snapshots/<ts>.tar.zst  # rotated prior saves (retention=3)

Concurrency: one lock serializes commit+save so the atomic rename on
latest.tar.zst is uncontested and `docker commit`s don't race.
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

# Tracks the last fingerprint we saved to EFS. Only save() consults this —
# commit() is cheap enough to run every time without a guard. Resets to ""
# after a successful save so the next diff can be compared against it.
_last_saved_fingerprint: str = ""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _list_tool_containers(running_only: bool) -> list[dict]:
    """Return [{id, name, created}] for every container whose name starts with hermes-.

    We can't filter by label because upstream doesn't label its tool containers;
    we can't filter by ancestor because after the first commit every container
    is an ancestor of doh-toolbox:latest (which we want).

    --no-trunc returns the full 64-char container id — required because
    `docker events` also emits full ids, and _reap_older_siblings compares
    them for equality. A short id match would reap the very container that
    just started.
    """
    flags = ["--no-trunc", "--format", "{{.ID}}\t{{.Names}}\t{{.CreatedAt}}"]
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
        # Container gone between listing and diff — caller handles.
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


def do_commit(container_id: str, trigger: str) -> bool:
    """Commit the container's writable layer onto TOOLBOX_TAG.

    Fast: no I/O, no compression. Adds one overlay2 layer on top of the
    existing chain; do_save flattens the chain back to one layer so the
    overlay2 127-layer cap is not a concern.

    Called on every die/orphan-reap/periodic/sigterm trigger — the tag is
    always fresh for the next `docker run` Hermes issues.
    """
    try:
        _run(["docker", "commit", container_id, TOOLBOX_TAG])
    except subprocess.CalledProcessError as e:
        LOG.error(
            "commit failed container=%s trigger=%s err=%s",
            container_id[:12], trigger, e.stderr.strip() if e.stderr else e,
        )
        return False
    LOG.info("commit ok container=%s trigger=%s tag=%s", container_id[:12], trigger, TOOLBOX_TAG)
    return True


def _flatten_toolbox_tag() -> bool:
    """Export TOOLBOX_TAG and re-import to collapse to a single overlay2 layer.

    Runs inside do_save so each EFS snapshot also bounds the in-daemon
    layer count (commit chains would otherwise approach overlay2's 127
    limit on a chatty bot). `docker export` drops image metadata; we
    re-apply the load-bearing ENV vars via `docker import --change` to
    match what was in the original base image.

    Sequence: create a throwaway container from the current tag, pipe its
    export straight into a new import that overwrites the tag, then rm
    the throwaway by its known id.
    """
    try:
        scratch_cid = _run(["docker", "create", TOOLBOX_TAG, "sleep", "infinity"]).stdout.strip()
    except subprocess.CalledProcessError as e:
        LOG.error("flatten: docker create failed err=%s", e.stderr.strip() if e.stderr else e)
        return False
    if not scratch_cid:
        LOG.error("flatten: docker create returned empty id")
        return False

    cmd = (
        f"docker export {scratch_cid} "
        f"| docker import "
        f"--change 'ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' "
        f"--change 'ENV LANG=C.UTF-8' "
        f"--change 'ENV POETRY_HOME=/usr/local' "
        f"- {TOOLBOX_TAG}"
    )
    ok = True
    try:
        subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        LOG.error("flatten: export|import failed err=%s", e.stderr.strip() if e.stderr else e)
        ok = False

    # Always clean up the scratch container by its known id, even if the
    # export|import failed — otherwise it accumulates across failed saves.
    subprocess.run(["docker", "rm", "-f", scratch_cid], capture_output=True, text=True)
    return ok


def do_save(trigger: str) -> bool:
    """Flatten TOOLBOX_TAG, save it to EFS as latest.tar.zst, rotate history.

    Must be preceded by do_commit so TOOLBOX_TAG reflects the live
    container's writable layer. Atomic-rename through incoming.tar.zst so
    a mid-write crash leaves latest.tar.zst intact.
    """
    started = time.monotonic()

    if not _flatten_toolbox_tag():
        return False

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    if INCOMING.exists():
        INCOMING.unlink()

    # `docker save` writes the full image (metadata + layers) as a tar
    # stream on stdout; zstd compresses into place on EFS.
    cmd = f"docker save {TOOLBOX_TAG} | zstd -T0 -q -o {INCOMING}"
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        LOG.error("save failed trigger=%s err=%s", trigger, e)
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

    LATEST_META.write_text(json.dumps({
        "base_image_ref": TOOL_IMAGE_BASE,
        "created_utc": ts,
        "size_bytes": size_bytes,
        "trigger": trigger,
    }, indent=2))
    _prune_snapshots()

    duration_ms = int((time.monotonic() - started) * 1000)
    LOG.info("save ok trigger=%s size=%d ms=%d", trigger, size_bytes, duration_ms)
    return True


def commit_and_maybe_save(container_id: str, trigger: str, save: bool) -> None:
    """One-call wrapper used by every trigger path. Holds the snapshot lock
    so concurrent triggers don't race on TOOLBOX_TAG or latest.tar.zst."""
    global _last_saved_fingerprint
    with _snapshot_lock:
        if not do_commit(container_id=container_id, trigger=trigger):
            return
        if not save:
            return

        # Guard EFS save by fingerprint: if nothing has changed since the
        # last save, skip the I/O. The commit itself already ran (cheap).
        fingerprint = _diff_fingerprint(container_id)
        if fingerprint and fingerprint == _last_saved_fingerprint:
            LOG.info(
                "save skipped (unchanged) container=%s trigger=%s",
                container_id[:12], trigger,
            )
            return
        if do_save(trigger=trigger):
            _last_saved_fingerprint = fingerprint


def _reap_older_siblings(newest: dict) -> None:
    """Commit + remove every running hermes-* container older than `newest`.

    Triggered on `docker events start` for a new tool container. Hermes
    restarted (same DinD) and spawned a fresh container; we capture the
    previous one's state onto TOOLBOX_TAG before removing it, and leave
    the newly-started container alone.
    """
    siblings = [c for c in _list_tool_containers(running_only=True) if c["id"] != newest["id"]]
    for c in siblings:
        LOG.info("reaping orphan tool container %s (%s)", c["id"][:12], c["name"])
        commit_and_maybe_save(container_id=c["id"], trigger="orphan-reap", save=False)
        try:
            _run(["docker", "rm", "-f", c["id"]])
        except subprocess.CalledProcessError as e:
            LOG.error("rm -f failed for orphan %s: %s", c["id"][:12], e)


def events_stream() -> None:
    """Long-poll `docker events` and dispatch start/die to reap/commit."""
    # event=create isn't enough — we need the container to be findable in
    # `docker ps`, which happens on start. die fires before rm, so we can
    # still docker-commit the stopped-but-present filesystem.
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
                    commit_and_maybe_save(
                        container_id=cid, trigger="die-event", save=False,
                    )
            except Exception as e:
                # Per-event guard: a single malformed event or failed commit
                # must not kill the stream. The timer loop is a fallback for
                # anything we miss here, but losing the stream entirely would
                # hide orphan accumulation.
                LOG.exception("event handling failed: %s", e)
    finally:
        proc.terminate()


def timer_loop() -> None:
    """Periodic commit + save for long-running containers. Commits are
    cheap so we do them every tick; the save path fingerprint-guards EFS
    I/O when the container is idle."""
    while not _shutdown.wait(SNAPSHOT_INTERVAL_SECONDS):
        try:
            for c in _list_tool_containers(running_only=True):
                commit_and_maybe_save(
                    container_id=c["id"], trigger="periodic", save=True,
                )
        except Exception as e:
            LOG.exception("timer_loop iteration failed: %s", e)


def _sigterm_snapshot_all() -> None:
    """Commit + save every live tool container. Called from the SIGTERM
    handler only, so unconditional (no docker-diff guard) — this is the
    last chance to persist before ECS kills us."""
    global _last_saved_fingerprint
    try:
        containers = _list_tool_containers(running_only=True)
    except subprocess.CalledProcessError as e:
        LOG.error("could not list tool containers during sigterm: %s", e)
        return
    for c in containers:
        with _snapshot_lock:
            if not do_commit(container_id=c["id"], trigger="sigterm"):
                continue
            if do_save(trigger="sigterm"):
                _last_saved_fingerprint = _diff_fingerprint(c["id"])


def _stop_dockerd() -> None:
    """Best-effort graceful dockerd shutdown after SIGTERM snapshotting.
    Waits up to 30s for dockerd to exit. ECS will SIGKILL us after
    stopTimeout (120s) regardless."""
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


def _handle_sigterm(signum: int, _frame: object) -> None:
    LOG.info("received signal %d; starting shutdown sequence", signum)
    _shutdown.set()
    _sigterm_snapshot_all()
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

    threading.Thread(target=events_stream, name="events", daemon=True).start()
    threading.Thread(target=timer_loop, name="timer", daemon=True).start()

    LOG.info(
        "doh-dind snapshotter started (persistence=%s, interval=%ds, dockerd_pid=%d)",
        PERSISTENCE_DIR, SNAPSHOT_INTERVAL_SECONDS, DOCKERD_PID,
    )
    while not _shutdown.is_set():
        _shutdown.wait(60)


if __name__ == "__main__":
    main()
