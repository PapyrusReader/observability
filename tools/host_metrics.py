#!/usr/bin/env python3
"""Collect bounded host metadata and stop log storage before it consumes app space."""
import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

GIB = 1024 ** 3
STATE = Path('/var/lib/papyrus-observability')
EXPECTED = {
    'papyrus-edge': ['proxy'],
    'papyrus-production': ['api', 'database', 'powersync-storage', 'powersync', 'web'],
    'papyrus-website': ['website'],
    'papyrus-observability': ['grafana', 'prometheus', 'loki', 'alloy'],
}
VOLUMES = {
    'database': 'papyrus-production_database',
    'sync_database': 'papyrus-production_powersync-storage',
    'media': 'papyrus-production_media',
    'loki': 'papyrus-observability-loki',
    'prometheus': 'papyrus-observability-prometheus',
    'alloy': 'papyrus-observability-alloy',
}


def run(*args):
    return subprocess.check_output(args, text=True, timeout=30)


def disk_guard_required(free_bytes, loki_bytes):
    return free_bytes < 8 * GIB or loki_bytes > 6 * GIB


def atomic_write(path, text):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(text)
    temporary.chmod(0o644)
    temporary.replace(path)


def observations(containers, previous):
    result = {}
    for container in containers:
        labels = container['Config'].get('Labels') or {}
        project = labels.get('com.docker.compose.project', '')
        service = labels.get('com.docker.compose.service', '')
        if service not in EXPECTED.get(project, []):
            continue
        key = project + '/' + service
        state = container['State']
        old = previous.get(key)
        restarts = container['RestartCount']
        cumulative = old['restarts'] if old else 0
        if old:
            cumulative += max(0, restarts - old['last_restart']) if old['id'] == container['Id'] else 1
        oom = int(state.get('OOMKilled', False))
        result[key] = {
            'id': container['Id'], 'last_restart': restarts, 'restarts': cumulative,
            'healthy': int(state['Running'] and state.get('Health', {}).get('Status', 'healthy') == 'healthy'),
            'oom': oom, 'oom_total': (old.get('oom_total', 0) if old else 0) + int(oom and not (old or {}).get('oom')),
        }
    return result


def collect(resume=False):
    STATE.mkdir(parents=True, exist_ok=True)
    output = STATE / 'textfile'
    output.mkdir(exist_ok=True)
    previous_path = STATE / 'containers.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    ids = run('docker', 'ps', '-aq').split()
    containers = json.loads(run('docker', 'inspect', *ids)) if ids else []
    current = observations(containers, previous)
    metrics = []
    for project, services in EXPECTED.items():
        for service in services:
            state = current.get(project + '/' + service, {})
            labels = f'project="{project}",service="{service}"'
            for name, field in [('healthy', 'healthy'), ('restarts_total', 'restarts'), ('oom_total', 'oom_total')]:
                metrics.append(f'papyrus_container_{name}{{{labels}}} {state.get(field, 0)}')
    sizes_path = STATE / 'sizes.json'
    sizes = json.loads(sizes_path.read_text()) if sizes_path.exists() else {'measured_at': 0, 'bytes': {}}
    refresh = time.time() - sizes['measured_at'] >= 600
    for label, volume in VOLUMES.items():
        if refresh or label == 'loki':
            path = run('docker', 'volume', 'inspect', '--format', '{{.Mountpoint}}', volume).strip()
            sizes['bytes'][label] = int(run('du', '-s', '-B1', path).split()[0])
    if refresh:
        sizes['measured_at'] = time.time()
    atomic_write(sizes_path, json.dumps(sizes))
    for label, size in sizes['bytes'].items():
        metrics.append(f'papyrus_storage_bytes{{kind="{label}"}} {size}')
    free = shutil.disk_usage('/').free
    blocked = disk_guard_required(free, sizes['bytes']['loki'])
    marker = STATE / 'loki-stopped'
    if blocked:
        marker.touch(exist_ok=True)
        loki_ids = [item['Id'] for item in containers if item['Config'].get('Labels', {}).get('com.docker.compose.project') == 'papyrus-observability' and item['Config'].get('Labels', {}).get('com.docker.compose.service') == 'loki' and item['State']['Running']]
        if loki_ids:
            subprocess.run(['docker', 'stop', *loki_ids], check=True, timeout=30, stdout=subprocess.DEVNULL)
            print('Loki stopped by disk guard; restore capacity before deliberate recovery.', flush=True)
    elif resume:
        marker.unlink(missing_ok=True)
    for line in Path('/proc/1/net/dev').read_text().splitlines()[2:]:
        name, fields = line.split(':', 1)
        if name.strip() in ('eth0', 'ens3', 'enp1s0'):
            values = fields.split()
            metrics += [f'papyrus_host_network_receive_bytes_total{{device="{name.strip()}"}} {values[0]}', f'papyrus_host_network_transmit_bytes_total{{device="{name.strip()}"}} {values[8]}']
    metrics += [f'papyrus_loki_disk_guard_active {int(marker.exists())}', f'papyrus_host_collector_timestamp_seconds {time.time()}', f'papyrus_root_available_bytes {free}']
    atomic_write(previous_path, json.dumps(current))
    atomic_write(output / 'papyrus.prom', '\n'.join(metrics) + '\n')
    if resume and marker.exists():
        raise SystemExit('Disk guard is still active; recovery refused.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', action='store_true')
    collect(parser.parse_args().resume)
