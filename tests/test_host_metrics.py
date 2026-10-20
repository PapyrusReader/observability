import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('host_metrics', Path(__file__).resolve().parents[1] / 'tools/host_metrics.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HostMetricsTests(unittest.TestCase):
    def test_log_guard_uses_both_capacity_conditions(self):
        self.assertFalse(module.disk_guard_required(9 * module.GIB, 5 * module.GIB))
        self.assertTrue(module.disk_guard_required(7 * module.GIB, 5 * module.GIB))
        self.assertTrue(module.disk_guard_required(9 * module.GIB, 7 * module.GIB))

    def container(self, identifier='one', restarts=0, running=True, healthy='healthy', oom=False):
        return {'Id': identifier, 'RestartCount': restarts, 'Config': {'Labels': {'com.docker.compose.project': 'papyrus-production', 'com.docker.compose.service': 'api'}}, 'State': {'Running': running, 'Health': {'Status': healthy}, 'OOMKilled': oom}}

    def test_restarts_survive_container_recreation(self):
        first = module.observations([self.container()], {})
        restarted = module.observations([self.container(restarts=3)], first)
        recreated = module.observations([self.container(identifier='two')], restarted)
        self.assertEqual(recreated['papyrus-production/api']['restarts'], 4)

    def test_oom_is_counted_once_and_unhealthy_is_not_healthy(self):
        failed = self.container(running=False, healthy='unhealthy', oom=True)
        first = module.observations([failed], {})
        second = module.observations([failed], first)
        self.assertEqual(second['papyrus-production/api']['oom_total'], 1)
        self.assertEqual(second['papyrus-production/api']['healthy'], 0)

    def test_unrelated_containers_are_excluded(self):
        container = self.container()
        container['Config']['Labels']['com.docker.compose.project'] = 'unrelated'
        self.assertEqual(module.observations([container], {}), {})
