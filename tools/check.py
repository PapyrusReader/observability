#!/usr/bin/env python3
"""Validate deployment boundaries and the pinned services' native configurations."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command):
    subprocess.run(command, check=True, cwd=ROOT)


def main():
    images = json.loads((ROOT / 'images.lock.json').read_text())
    command = ['docker', 'compose', '--env-file', 'monitoring/monitoring.env.example', '-f', 'monitoring/compose.yml', 'config', '--format', 'json']
    config = json.loads(subprocess.check_output(command, cwd=ROOT))
    assert config['name'] == 'papyrus-observability'
    assert set(config['services']) == {'grafana', 'prometheus', 'loki', 'alloy'}
    for name, service in config['services'].items():
        assert not service.get('ports'), name
        assert service['image'] == images[name] and '@sha256:' in service['image']
        assert int(service['mem_limit']) <= 512 * 1024 ** 2
        assert float(service['cpus']) <= 0.25
        assert bool(service.get('privileged')) == (name == 'alloy')
    assert set(config['services']['prometheus']['networks']) == {'monitoring'}
    assert set(config['services']['loki']['networks']) == {'monitoring'}
    assert config['services']['grafana']['environment']['GF_AUTH_ANONYMOUS_ENABLED'] == 'false'
    assert config['services']['grafana']['environment']['GF_USERS_ALLOW_SIGN_UP'] == 'false'
    checks = [
        ('prometheus', ['--entrypoint', '/bin/promtool'], ['check', 'config', '/repo/monitoring/prometheus.yml']),
        ('loki', [], ['-config.file=/repo/monitoring/loki.yml', '-verify-config=true']),
        ('alloy', [], ['validate', '/repo/monitoring/config.alloy']),
    ]
    for name, options, args in checks:
        run(['docker', 'run', '--rm', '-v', f'{ROOT}:/repo:ro', *options, images[name], *args])
    run(['docker', 'run', '--rm', '-v', f'{ROOT}/edge/Caddyfile:/etc/caddy/Caddyfile:ro', '-e', 'ACME_EMAIL=monitor@example.org', '-e', 'WEBSITE_DOMAIN=example.org', '-e', 'APP_DOMAIN=app.example.org', '-e', 'API_DOMAIN=api.example.org', '-e', 'SYNC_DOMAIN=sync.example.org', 'caddy:2.10.2-alpine', 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
    print('Compose boundaries and native configurations validated.')


if __name__ == '__main__':
    main()
