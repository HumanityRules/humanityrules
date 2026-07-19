"""Tests for node_packing: profile invariants, slot arithmetic, and marker resolution."""

from django.test import SimpleTestCase

from humanityrules_app.management.commands import seed_app_templates
from humanityrules_app.services.infra_customer import node_packing


def _hermes_template_containers() -> list[dict]:
    return seed_app_templates.HERMES_PERSONAL_TEMPLATE["containers"]


class NodeProfileTests(SimpleTestCase):

    def test_profiles_fit_their_eni_ceilings(self) -> None:
        for profile in (node_packing.STANDARD_PROFILE, node_packing.DENSE_PROFILE):
            geometry = node_packing.INSTANCE_GEOMETRY[profile.instance_type]
            if profile.requires_eni_trunking:
                ceiling = geometry.max_tasks_with_trunking
            else:
                ceiling = geometry.max_tasks_without_trunking
            self.assertIsNotNone(ceiling)
            self.assertLessEqual(profile.tasks_per_node, ceiling)

    def test_standard_profile_slot_is_half_a_t4g_large(self) -> None:
        slot = node_packing.task_slot(profile=node_packing.STANDARD_PROFILE)
        self.assertEqual(slot, node_packing.TaskSlot(cpu=1024, memory_mib=3584))


class ResolveSlotFillersTests(SimpleTestCase):

    def test_standard_resolution_reproduces_pre_node_packing_task_totals(self) -> None:
        # The regression gate for introducing the node-packing machinery: on
        # the standard profile the resolved task totals must equal the values
        # previously hardcoded in the template (half a t4g.large), so shipping
        # the machinery changed no task placement anywhere.
        resolved = node_packing.resolve_slot_fillers(containers=_hermes_template_containers(), eni_trunking_enabled=False)
        hermes = next(c for c in resolved if c["name"] == "hermes")
        proxy = next(c for c in resolved if c["name"] == "policy-proxy")

        self.assertNotIn("fills_node_slot", hermes)
        self.assertEqual(hermes["cpu_reservation"] + proxy["cpu_reservation"], 1024)
        self.assertEqual(hermes["memory_reservation_mib"] + proxy["memory_limit_mib"], 3584)
        self.assertEqual(hermes["memory_limit_mib"], 4096)

    def test_dense_resolution_packs_eight_tasks_per_r8g_large(self) -> None:
        resolved = node_packing.resolve_slot_fillers(containers=_hermes_template_containers(), eni_trunking_enabled=True)
        hermes = next(c for c in resolved if c["name"] == "hermes")
        proxy = next(c for c in resolved if c["name"] == "policy-proxy")

        geometry = node_packing.INSTANCE_GEOMETRY[node_packing.DENSE_PROFILE.instance_type]
        per_task_cpu = hermes["cpu_reservation"] + proxy["cpu_reservation"]
        per_task_mem = hermes["memory_reservation_mib"] + proxy["memory_limit_mib"]
        tasks = node_packing.DENSE_PROFILE.tasks_per_node
        self.assertLessEqual(per_task_cpu * tasks, geometry.allocatable_cpu)
        self.assertLessEqual(per_task_mem * tasks, geometry.allocatable_memory_mib)
        # The floor must clear the worst hermes peak ever observed (~1.2 GiB);
        # a denser profile that dips under it needs a new memory analysis.
        self.assertGreaterEqual(hermes["memory_reservation_mib"], 1280)

    def test_resolution_does_not_mutate_the_template(self) -> None:
        containers = _hermes_template_containers()
        node_packing.resolve_slot_fillers(containers=containers, eni_trunking_enabled=False)
        hermes = next(c for c in containers if c["name"] == "hermes")
        self.assertNotIn("cpu_reservation", hermes)
        self.assertTrue(hermes["fills_node_slot"])

    def test_containers_without_marker_pass_through_unchanged(self) -> None:
        containers = [{"name": "app", "cpu_reservation": 512, "memory_limit_mib": 512}]
        resolved = node_packing.resolve_slot_fillers(containers=containers, eni_trunking_enabled=False)
        self.assertIs(resolved, containers)

    def test_two_fillers_rejected(self) -> None:
        containers = [{"name": "a", "fills_node_slot": True}, {"name": "b", "fills_node_slot": True}]
        with self.assertRaisesRegex(ValueError, "At most one container"):
            node_packing.resolve_slot_fillers(containers=containers, eni_trunking_enabled=False)

    def test_filler_with_explicit_reservation_rejected(self) -> None:
        containers = [{"name": "a", "fills_node_slot": True, "cpu_reservation": 512}]
        with self.assertRaisesRegex(ValueError, "one or the other"):
            node_packing.resolve_slot_fillers(containers=containers, eni_trunking_enabled=False)

    def test_sibling_without_reservations_rejected(self) -> None:
        containers = [{"name": "a", "fills_node_slot": True}, {"name": "b"}]
        with self.assertRaisesRegex(ValueError, "needs cpu_reservation"):
            node_packing.resolve_slot_fillers(containers=containers, eni_trunking_enabled=False)

    def test_siblings_exceeding_the_slot_rejected(self) -> None:
        containers = [
            {"name": "a", "fills_node_slot": True},
            {"name": "b", "cpu_reservation": 2048, "memory_limit_mib": 128},
        ]
        with self.assertRaisesRegex(ValueError, "leave no room"):
            node_packing.resolve_slot_fillers(containers=containers, eni_trunking_enabled=False)
