"""Node packing: how many Hermes tasks share one EC2 container instance, and how big each is.

This module is the single home for the packing model. Everything else consumes
its outputs: seed_app_templates declares needs (absolute reservations, or the
fills_node_slot marker), app_config_builder resolves the marker into concrete
reservations at deploy time, and deploy_base asks it for the instance type.

The model:

- In awsvpc mode every task gets its own ENI. A `.large` instance has 3 ENIs
  (one for the host), so 2 tasks per node is the ceiling regardless of CPU or
  memory — unless the account enables ECS `awsvpcTrunking` AND the instance
  family supports trunk ENIs (the t-family does not). We never require trunking
  from customer accounts; Environment.eni_trunking_enabled records the choice
  per environment at creation time.

- A node profile is one coherent judgment: an instance type plus how many task
  slots it is divided into. tasks_per_node is deliberately below the trunked
  ENI ceiling — it is chosen from memory comfort (each slot's floor must clear
  observed hermes peaks: RSS runs 500-1000 MiB, worst peak ~1.2 GiB), not from
  what ENIs allow.

- A slot is allocatable geometry divided by tasks_per_node. The slot-filler
  container claims the slot minus its siblings' reservations. Reservations are
  floors, not caps: CPU shares let a task burst to the whole node when
  neighbors are idle, and memory reservations become memory.low floors. Because
  slots partition the node, floors sum to at most the host's allocatable
  memory — under host pressure the kernel reclaims from (and if needed
  OOM-kills) whichever container is above its floor, never one below it. That
  invariant holds by construction here; it used to be hand-verified prose in
  seed_app_templates.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeGeometry:
    """Physical facts about an instance type, mirrored from AWS."""

    allocatable_cpu: int
    allocatable_memory_mib: int
    # awsvpc task ceilings from ENI counts; None = family has no trunk ENI support.
    max_tasks_without_trunking: int
    max_tasks_with_trunking: int | None


@dataclass(frozen=True)
class NodeProfile:
    """One packing judgment: instance type + how many task slots it is divided into."""

    instance_type: str
    tasks_per_node: int
    requires_eni_trunking: bool


@dataclass(frozen=True)
class TaskSlot:
    """Per-task share of a node under a profile."""

    cpu: int
    memory_mib: int


# Allocatable = nominal minus ~1 GiB host reserve (ECS agent + OS), matching the
# ~7.6 GiB the ECS agent registers on a live 8 GiB t4g.large. The r8g figure is
# extrapolated the same way — verify against describe-container-instances on a
# real node before first dense rollout.
INSTANCE_GEOMETRY = {
    "t4g.large": NodeGeometry(allocatable_cpu=2048, allocatable_memory_mib=7168, max_tasks_without_trunking=2, max_tasks_with_trunking=None),
    "m8g.large": NodeGeometry(allocatable_cpu=2048, allocatable_memory_mib=7168, max_tasks_without_trunking=2, max_tasks_with_trunking=10),
    "r8g.large": NodeGeometry(allocatable_cpu=2048, allocatable_memory_mib=15360, max_tasks_without_trunking=2, max_tasks_with_trunking=10),
}

STANDARD_PROFILE = NodeProfile(instance_type="t4g.large", tasks_per_node=2, requires_eni_trunking=False)
# Which dense profile is live is a static choice for now; m8g.large @ 4 is the
# one-line fallback if 8 HAs on 2 vCPU contend in practice.
DENSE_PROFILE = NodeProfile(instance_type="r8g.large", tasks_per_node=8, requires_eni_trunking=True)


def profile_for(eni_trunking_enabled: bool) -> NodeProfile:
    """Map an environment's trunking flag to the node profile it deploys under."""
    return DENSE_PROFILE if eni_trunking_enabled else STANDARD_PROFILE


def task_slot(profile: NodeProfile) -> TaskSlot:
    """Per-task share of a node: allocatable geometry divided by tasks_per_node."""
    geometry = INSTANCE_GEOMETRY[profile.instance_type]
    return TaskSlot(
        cpu=geometry.allocatable_cpu // profile.tasks_per_node,
        memory_mib=geometry.allocatable_memory_mib // profile.tasks_per_node,
    )


def resolve_slot_fillers(containers: list[dict], eni_trunking_enabled: bool) -> list[dict]:
    """Replace a fills_node_slot marker with concrete reservations for the env's profile.

    Returns the container dicts unchanged when no container carries the marker.
    The filler receives cpu_reservation and memory_reservation_mib equal to one
    task slot minus its siblings' reservations (a sibling's memory contribution
    is memory_reservation_mib, falling back to memory_limit_mib — the same
    fallback ECS uses for placement).
    """
    fillers = [c for c in containers if c.get("fills_node_slot")]
    if not fillers:
        return containers
    if len(fillers) > 1:
        names = ", ".join(c["name"] for c in fillers)
        raise ValueError(f"At most one container may set fills_node_slot; got: {names}")

    filler = fillers[0]
    if filler.get("cpu_reservation") is not None or filler.get("memory_reservation_mib") is not None:
        raise ValueError(
            f"Container '{filler['name']}' sets fills_node_slot and explicit reservations; "
            "declare one or the other"
        )

    sibling_cpu = 0
    sibling_memory_mib = 0
    for sibling in containers:
        if sibling is filler:
            continue
        cpu = sibling.get("cpu_reservation")
        memory_mib = sibling.get("memory_reservation_mib") or sibling.get("memory_limit_mib")
        if cpu is None or memory_mib is None:
            raise ValueError(
                f"Container '{sibling['name']}' needs cpu_reservation and a memory "
                "reservation or limit to share a task with a fills_node_slot container"
            )
        sibling_cpu += cpu
        sibling_memory_mib += memory_mib

    slot = task_slot(profile=profile_for(eni_trunking_enabled=eni_trunking_enabled))
    filler_cpu = slot.cpu - sibling_cpu
    filler_memory_mib = slot.memory_mib - sibling_memory_mib
    if filler_cpu <= 0 or filler_memory_mib <= 0:
        raise ValueError(
            f"Sibling reservations ({sibling_cpu} CPU, {sibling_memory_mib} MiB) leave no room "
            f"in a task slot of {slot.cpu} CPU / {slot.memory_mib} MiB"
        )

    resolved = dict(filler)
    del resolved["fills_node_slot"]
    resolved["cpu_reservation"] = filler_cpu
    resolved["memory_reservation_mib"] = filler_memory_mib
    return [resolved if c is filler else c for c in containers]


def _assert_valid(profile: NodeProfile) -> None:
    """Fail at import if a profile contradicts the geometry it packs onto."""
    geometry = INSTANCE_GEOMETRY[profile.instance_type]
    if profile.requires_eni_trunking:
        assert geometry.max_tasks_with_trunking is not None, f"{profile.instance_type} does not support ENI trunking"
        assert profile.tasks_per_node <= geometry.max_tasks_with_trunking
    else:
        assert profile.tasks_per_node <= geometry.max_tasks_without_trunking


_assert_valid(profile=STANDARD_PROFILE)
_assert_valid(profile=DENSE_PROFILE)
