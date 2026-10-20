import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AlertExpressionTests(unittest.TestCase):
    def test_memory_and_container_alerts_clear_and_fire(self):
        document = json.loads((ROOT / 'monitoring/grafana/provisioning/alerting/rules.yml').read_text())
        rules = {rule['uid']: rule['data'][0]['model']['expr'] for rule in document['groups'][0]['rules']}
        cases = []
        for minute, expected in [(0, 0), (1, 1), (2, 0)]:
            cases += [
                {'expr': rules['papyrus-memory-low'], 'eval_time': f'{minute}m', 'exp_samples': [{'labels': '{}', 'value': expected}]},
                {'expr': rules['papyrus-container-unhealthy'], 'eval_time': f'{minute}m', 'exp_samples': [{'labels': '{project="papyrus-production",service="api"}', 'value': expected}]},
                {'expr': rules['papyrus-db-connections'], 'eval_time': f'{minute}m', 'exp_samples': [{'labels': '{instance="postgresql://database:5432/papyrus"}', 'value': expected}]},
            ]
        fixture = {'evaluation_interval': '1m', 'tests': [{'interval': '1m', 'input_series': [
            {'series': 'node_memory_MemTotal_bytes', 'values': '1000 1000 1000'},
            {'series': 'node_memory_MemAvailable_bytes', 'values': '800 50 800'},
            {'series': 'papyrus_container_healthy{project="papyrus-production",service="api"}', 'values': '1 0 1'},
            {'series': 'pg_stat_database_numbackends{instance="postgresql://database:5432/papyrus",datname="papyrus"}', 'values': '5 90 5'},
            {'series': 'pg_settings_max_connections{instance="postgresql://database:5432/papyrus",server="database:5432"}', 'values': '100 100 100'},
        ], 'promql_expr_test': cases}]}
        image = json.loads((ROOT / 'images.lock.json').read_text())['prometheus']
        with tempfile.TemporaryDirectory() as directory:
            Path(directory).chmod(0o755)
            (Path(directory) / 'alerts.yml').write_text(json.dumps(fixture))
            subprocess.run(['docker', 'run', '--rm', '-v', directory + ':/tests:ro', '--entrypoint', '/bin/promtool', image, 'test', 'rules', '/tests/alerts.yml'], check=True)
