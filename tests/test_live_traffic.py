import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.render import ALERTS, PUBLIC_HOSTS, live_traffic_dashboard

ROOT = Path(__file__).resolve().parents[1]


class LiveTrafficTests(unittest.TestCase):
    def test_completed_requests_statuses_and_exclusions(self):
        panels = {panel['title']: panel for panel in live_traffic_dashboard()['panels']}
        host = 'papyrus-reader.com'
        labels = f'job="services",handler="subroute",host="{host}"'
        metric = 'caddy_http_request_duration_seconds_count'
        def expression(title):
            return panels[title]['targets'][0]['expr'].replace('${host:regex}', PUBLIC_HOSTS)
        series = [
            {'series': metric + '{' + labels + ',code="200"}', 'values': '0+8x60'},
            {'series': metric + '{' + labels + ',code="500"}', 'values': '0+2x60'},
            {'series': metric + '{job="services",handler="reverse_proxy",host="papyrus-reader.com",code="200"}', 'values': '0+100x60'},
            {'series': metric + '{job="services",handler="subroute",host="monitor.papyrus-reader.com",code="200"}', 'values': '0+100x60'},
            {'series': metric + '{job="services",handler="metrics",host="proxy:9180",code="200"}', 'values': '0+100x60'},
            {'series': metric + '{job="services",handler="static_response",host="papyrus-reader.com",code="308"}', 'values': '0+100x60'},
            {'series': 'caddy_http_requests_in_flight{' + labels + '}', 'values': '2+0x60'},
        ]
        cases = [
            {'expr': expression('Requests in the last minute'), 'eval_time': '1m', 'exp_samples': [{'labels': '{}', 'value': 60}]},
            {'expr': expression('Server errors in the last minute'), 'eval_time': '1m', 'exp_samples': [{'labels': '{}', 'value': 12}]},
            {'expr': expression('Requests in flight'), 'eval_time': '1m', 'exp_samples': [{'labels': '{}', 'value': 2}]},
            {'expr': expression('Responses by status'), 'eval_time': '1m', 'exp_samples': [
                {'labels': '{host="papyrus-reader.com",code="200"}', 'value': 0.8},
                {'labels': '{host="papyrus-reader.com",code="500"}', 'value': 0.2},
            ]},
        ]
        alert = next(rule[2] for rule in ALERTS if rule[0] == 'http-errors')
        cases.append({'expr': alert, 'eval_time': '10m', 'exp_samples': [{'labels': '{host="papyrus-reader.com"}', 'value': 120}]})
        fixture = {'evaluation_interval': '10s', 'tests': [{'interval': '10s', 'input_series': series, 'promql_expr_test': cases},
            {'interval': '10s', 'input_series': series[:1], 'promql_expr_test': [
                {'expr': expression('Server errors in the last minute'), 'eval_time': '1m', 'exp_samples': [{'labels': '{}', 'value': 0}]},
                {'expr': alert, 'eval_time': '10m', 'exp_samples': []},
            ]}]}
        image = json.loads((ROOT / 'images.lock.json').read_text())['prometheus']
        with tempfile.TemporaryDirectory() as directory:
            Path(directory).chmod(0o755)
            (Path(directory) / 'traffic.yml').write_text(json.dumps(fixture))
            subprocess.run(['docker', 'run', '--rm', '-v', directory + ':/tests:ro', '--entrypoint', '/bin/promtool', image, 'test', 'rules', '/tests/traffic.yml'], check=True)
