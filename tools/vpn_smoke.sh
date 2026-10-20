#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
docker run --rm -i --cap-add NET_ADMIN -v "$root/vpn:/vpn:ro" \
  alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8 \
  sh -c 'apk add --no-cache python3 wireguard-tools iptables dnsmasq >/dev/null && dnsmasq --test --conf-file=/vpn/dnsmasq.conf && python3 -' <<'PY'
import json
import subprocess
from pathlib import Path

def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout

run('python3', '/vpn/manage.py', 'init')
key = Path('/etc/wireguard/papyrus/server.key').read_text()
run('python3', '/vpn/manage.py', 'init')
assert Path('/etc/wireguard/papyrus/server.key').read_text() == key
run('ip', 'link', 'add', 'wgmonitor', 'type', 'wireguard')
run('wg', 'setconf', 'wgmonitor', '/etc/wireguard/papyrus/runtime.conf')
run('python3', '/vpn/manage.py', 'add-peer', 'mac', '10.77.0.2')
run('python3', '/vpn/manage.py', 'add-peer', 'phone', '10.77.0.3')
assert len(run('wg', 'show', 'wgmonitor', 'peers').split()) == 2
for path in [Path('/etc/wireguard/wgmonitor.conf'), *Path('/etc/wireguard/papyrus/profiles').glob('*.conf')]:
    assert path.stat().st_mode & 0o777 == 0o600
result = subprocess.run(['python3', '/vpn/manage.py', 'add-peer', 'duplicate', '10.77.0.2'], capture_output=True)
assert result.returncode != 0
run('python3', '/vpn/manage.py', 'revoke-peer', 'mac')
assert len(run('wg', 'show', 'wgmonitor', 'peers').split()) == 1
assert set(json.loads(Path('/etc/wireguard/papyrus/peers.json').read_text())) == {'phone'}
assert not Path('/etc/wireguard/papyrus/profiles/mac.conf').exists()
run('ip', 'link', 'del', 'wgmonitor')
run('ip', 'link', 'add', 'wgmonitor', 'type', 'wireguard')
run('wg', 'setconf', 'wgmonitor', '/etc/wireguard/papyrus/runtime.conf')
assert len(run('wg', 'show', 'wgmonitor', 'peers').split()) == 1
run('iptables', '-N', 'DOCKER-USER')
run('sh', '/vpn/firewall.sh')
run('sh', '/vpn/firewall.sh')
assert run('iptables', '-S', 'DOCKER-USER').count('-j PAPYRUS-VPN') == 1
print('PASS: native WireGuard config, persistent keys, peer isolation, live revocation, private files, DNS and idempotent firewall.')
PY
