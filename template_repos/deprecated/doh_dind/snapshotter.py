#!/usr/bin/env python3
"""
doh-dind snapshotter: persists Hermes tool-container filesystem state across
container exits and ECS task replacements.

Runs as PID 1 inside the doh-dind container after /entrypoint.sh has staged
doh-toolbox:latest and started dockerd.

Operations:

  do_commit(container_id) - commit the container filesystem onto
                            doh-toolbox:latest so the next Hermes tool
                            container starts from the latest state.

  do_save()               - compact doh-toolbox:latest when its layer count
                            reaches the threshold, then stream
                            `docker save | zstd` to EFS.

Triggers:

  1. `docker events die`  - commit the stopped tool container (fast path,
                            ~100ms). EFS save deferred to the periodic
                            tick.
  2. Periodic timer       - every SAVE_INTERVAL_SECONDS, commit every
                            live tool container and save to EFS.
                            Catches long-running containers that never
                            exit between saves.
  3. SIGTERM              - same as periodic, on the shutdown path,
                            before stopping dockerd.

EFS layout (under $PERSISTENCE_DIR):

    latest.tar.zst          # `docker save` of doh-toolbox:latest
    latest.meta.json        # base_image_ref, ts, size_bytes, trigger
    incoming.tar.zst        # in-flight write; atomic-renamed to latest
    snapshots/<ts>.tar.zst  # rotated prior saves (retention=3)

Concurrency: one process-local lock serializes commits against saves so
the atomic rename on latest.tar.zst is uncontested inside this
snapshotter. A die event arriving during an in-flight save blocks on the
lock until the save finishes — acceptable because saves only run every
SAVE_INTERVAL_SECONDS.
"""

import datetime
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
RETENTION = 3
FLATTEN_LAYER_THRESHOLD = 32
SAVE_INTERVAL_SECONDS = 600

# Passed through from /entrypoint.sh via env so we only configure the daemon
# lifecycle in one place (the shell wrapper that started it).
DOCKERD_PID = int(os.environ.get("DOCKERD_PID", "0"))
TOOLBOX_TAG = os.environ.get("TOOLBOX_TAG", "doh-toolbox:latest")
TOOL_IMAGE_BASE = os.environ.get("TOOL_IMAGE_BASE", "")

_snapshot_lock = threading.Lock()
_shutdown = threading.Event()


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    # Pre-call log so we can reconcile our subprocess activity against
    # dockerd's debug log when diagnosing mystery stop/kill calls.
    LOG.info("subprocess: %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _run_shell(cmd: str) -> subprocess.CompletedProcess:
    """Run a shell pipeline and fail if any pipeline segment fails."""
    LOG.info("subprocess (shell): %s", cmd)
    return subprocess.run(args=["bash", "-o", "pipefail", "-c", cmd], check=True)


def _list_tool_containers(running_only: bool) -> list[dict]:
    """Return [{id, name, created}] for every container whose name starts with hermes-.

    We can't filter by label because upstream doesn't label its tool containers;
    we can't filter by ancestor because after the first commit every container
    is an ancestor of doh-toolbox:latest (which we want).

    --no-trunc returns the full 64-char container id so ids from `docker ps`
    and `docker events` use the same shape in logs and trigger handlers.
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


def _prune_snapshots() -> None:
    snaps = sorted(SNAPSHOTS_DIR.glob("*.tar.zst"))
    for old in snaps[:-RETENTION]:
        try:
            old.unlink()
        except OSError as e:
            LOG.error("prune: failed to remove %s: %s", old, e)


def _toolbox_layer_count() -> int | None:
    """Return the current Docker image layer count for TOOLBOX_TAG."""
    try:
        out = _run(cmd=[
            "docker",
            "image",
            "inspect",
            "--format",
            "{{len .RootFS.Layers}}",
            TOOLBOX_TAG,
        ]).stdout.strip()
    except subprocess.CalledProcessError as e:
        LOG.error("layer inspect failed tag=%s err=%s", TOOLBOX_TAG, e.stderr.strip() if e.stderr else e)
        return None
    try:
        return int(out)
    except ValueError:
        LOG.error("layer inspect returned non-integer tag=%s out=%r", TOOLBOX_TAG, out)
        return None


def do_commit(container_id: str, trigger: str) -> bool:
    """Commit the container's writable layer onto TOOLBOX_TAG.

    Fast: no I/O, no compression. Adds one overlay2 layer on top of the
    existing chain; do_save compacts the tag before the chain gets deep.

    Takes the snapshot lock so a concurrent save can't race the tag update.
    """
    with _snapshot_lock:
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

    `docker export` drops image metadata; we re-apply the load-bearing ENV
    vars via `docker import --change` to match what was in the original base
    image.

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
        _run_shell(cmd)
    except subprocess.CalledProcessError as e:
        LOG.error("flatten: export|import failed err=%s", e.stderr.strip() if e.stderr else e)
        ok = False

    # Always clean up the scratch container by its known id, even if the
    # export|import failed; otherwise it accumulates across failed saves.
    try:
        _run(["docker", "rm", "-f", scratch_cid])
    except subprocess.CalledProcessError as e:
        LOG.error("flatten: scratch cleanup failed id=%s err=%s", scratch_cid[:12], e.stderr.strip() if e.stderr else e)
    return ok


def do_save(trigger: str) -> bool:
    """Save TOOLBOX_TAG to EFS as latest.tar.zst and rotate history.

    Must be preceded by do_commit so TOOLBOX_TAG reflects the live
    container's writable layer. Atomic-rename through incoming.tar.zst so
    a mid-write crash leaves latest.tar.zst intact.

    Takes the snapshot lock so commits queue behind it.
    """
    with _snapshot_lock:
        started = time.monotonic()

        layer_count = _toolbox_layer_count()
        should_flatten = layer_count is None or layer_count >= FLATTEN_LAYER_THRESHOLD
        if should_flatten:
            LOG.info("flattening toolbox tag layer_count=%s threshold=%d", layer_count, FLATTEN_LAYER_THRESHOLD)
            if not _flatten_toolbox_tag():
                return False
            layer_count = _toolbox_layer_count()
        else:
            LOG.info("flatten skipped layer_count=%d threshold=%d", layer_count, FLATTEN_LAYER_THRESHOLD)

        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        if INCOMING.exists():
            INCOMING.unlink()

        # `docker save` writes the full image (metadata + layers) as a tar
        # stream on stdout; zstd compresses into place on EFS.
        cmd = f"docker save {TOOLBOX_TAG} | zstd -T0 -q -o {INCOMING}"
        try:
            _run_shell(cmd)
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
            "flattened": should_flatten,
            "layer_count": layer_count,
            "size_bytes": size_bytes,
            "trigger": trigger,
        }, indent=2))

        _prune_snapshots()

        duration_ms = int((time.monotonic() - started) * 1000)
        LOG.info(
            "save ok trigger=%s size=%d ms=%d flattened=%s layer_count=%s",
            trigger, size_bytes, duration_ms, should_flatten, layer_count,
        )
        return True


def _events_stream() -> None:
    """Long-poll `docker events` and persist stopped tool containers."""
    # die fires before rm, so the stopped container can still be committed.
    proc = subprocess.Popen(
        [
            "docker", "events", "--format", "{{json .}}",
            "--filter", "type=container",
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

                if status == "die":
                    do_commit(container_id=cid, trigger="die-event")

            except Exception as e:
                # A single malformed event or failed commit must not kill the
                # stream, because this is the primary persistence trigger.
                LOG.exception("event handling failed: %s", e)
    finally:
        proc.terminate()


def _commit_running_and_save(trigger: str) -> None:
    """Commit every live tool container, then save to EFS.

    Die events commit stopped containers; this handles the other case —
    long-running tool containers (TERMINAL_LIFETIME_SECONDS=86400) that
    accumulate writes without ever exiting. Without this, a multi-hour
    session would leak all its filesystem changes between restarts."""
    try:
        containers = _list_tool_containers(running_only=True)
    except subprocess.CalledProcessError as e:
        LOG.error("could not list tool containers (trigger=%s): %s", trigger, e)
        containers = []
    for c in containers:
        do_commit(container_id=c["id"], trigger=trigger)
    do_save(trigger=trigger)


def _periodic_save_loop() -> None:
    """Every SAVE_INTERVAL_SECONDS: commit live containers, then save.

    We commit running containers here (not just rely on die-event
    commits) so long-running tool containers still get their state
    captured."""
    while not _shutdown.wait(SAVE_INTERVAL_SECONDS):
        _commit_running_and_save(trigger="periodic")


def _sigterm_snapshot_all() -> None:
    """Persist every live tool container before dockerd is stopped."""
    _commit_running_and_save(trigger="sigterm")


def _stop_dockerd() -> None:
    """Best-effort graceful dockerd shutdown after SIGTERM snapshotting.
    Waits up to 15s for dockerd to exit, then SIGKILLs. moby v26 frequently
    finishes its shutdown work (logs "Daemon shutdown complete") but the
    process itself lingers waiting on goroutines that never return."""
    if DOCKERD_PID <= 0:
        return
    try:
        os.kill(DOCKERD_PID, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            os.kill(DOCKERD_PID, 0)
        except ProcessLookupError:
            return
        time.sleep(0.5)
    LOG.info("dockerd (pid=%d) did not exit within 15s after SIGTERM; sending SIGKILL", DOCKERD_PID)
    try:
        os.kill(DOCKERD_PID, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _handle_termination_signal(signum: int, _frame: object) -> None:
    LOG.info("received signal %d; starting shutdown sequence", signum)
    _shutdown.set()
    _sigterm_snapshot_all()
    _stop_dockerd()
    sys.exit(0)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s Snapshotter: %(message)s",
    )
    PERSISTENCE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    signal.signal(signal.SIGTERM, _handle_termination_signal)
    signal.signal(signal.SIGINT, _handle_termination_signal)

    threading.Thread(target=_events_stream, name="events", daemon=True).start()
    threading.Thread(target=_periodic_save_loop, name="periodic-save", daemon=True).start()

    LOG.info(
        "doh-dind snapshotter started (persistence=%s, dockerd_pid=%d, save_interval_s=%d)",
        PERSISTENCE_DIR, DOCKERD_PID, SAVE_INTERVAL_SECONDS,
    )
    # Signal handlers exit(0) the process directly; this wait is only unblocked by that path
    _shutdown.wait()


if __name__ == "__main__":
    main()
