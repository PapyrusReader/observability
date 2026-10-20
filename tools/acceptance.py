#!/usr/bin/env python3
"""Record a 24-hour production acceptance report without changing application state."""
import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from render import container_usage

STATE = Path('/var/lib/papyrus-observability')
CHECKS = {
    'available_memory': 'min_over_time(node_memory_MemAvailable_bytes[24h]) >= 536870912',
    'monitoring_oom_events': 'sum(increase(papyrus_container_oom_total{project="papyrus-observability"}[24h])) == 0',
    'required_scrapes': 'min(min_over_time(up[24h])) == 1',
    'container_resource_coverage': 'count(' + container_usage('container_memory_working_set_bytes{image!=""}') + ') >= scalar(count(papyrus_container_healthy))',
    'collector_freshness': 'max_over_time((time() - papyrus_host_collector_timestamp_seconds)[24h:1m]) < 180',
    'log_guard': 'max_over_time(papyrus_loki_disk_guard_active[24h]) == 0',
}


def main():
    address = subprocess.check_output(['docker', 'inspect', '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', 'papyrus-observability-prometheus-1'], text=True).strip()
    def query(expression):
        url = 'http://' + address + ':9090/api/v1/query?' + urllib.parse.urlencode({'query': expression})
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.load(response)['data']['result']
    started = float((STATE / 'observation-started').read_text())
    results = {name: bool(query(expression)) for name, expression in CHECKS.items()}
    results['full_window'] = time.time() - started >= 86400
    results['sample_coverage'] = bool(query('count_over_time(node_memory_MemAvailable_bytes[24h]) >= 2800'))
    measurements = {name: query(expression) for name, expression in {
        'active_series': 'prometheus_tsdb_head_series',
        'storage': 'papyrus_storage_bytes',
        'root_disk_growth_bytes_per_day': '-deriv(papyrus_root_available_bytes[24h]) * 86400',
        'log_ingestion_bytes_per_second': 'sum(rate(loki_distributor_bytes_received_total[1h]))',
    }.items()}
    report = {'checked_at': time.time(), 'observation_started': started, 'passed': all(results.values()), 'checks': results, 'measurements': measurements}
    temporary = STATE / 'acceptance.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(STATE / 'acceptance.json')
    textfile = STATE / 'textfile/acceptance.prom'
    temporary = textfile.with_suffix('.tmp')
    temporary.write_text(f'papyrus_acceptance_success {int(report["passed"])}\npapyrus_acceptance_timestamp_seconds {time.time()}\n')
    temporary.replace(textfile)
    print(json.dumps({'passed': report['passed'], 'checks': results}))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
