#!/usr/bin/env python3
"""Provision and revoke individual VPN peers without printing secret material."""
import argparse
import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path

STATE = Path('/etc/wireguard/papyrus')
CONFIG = Path('/etc/wireguard/wgmonitor.conf')


def private_write(path, content):
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(content)
    temporary.chmod(0o600)
    temporary.replace(path)


def command(*args, data=None):
    return subprocess.check_output(args, input=data, text=True).strip()


def validate_peer(name, address, peers):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', name):
        raise ValueError('Use a short lowercase peer name containing letters, digits or hyphens')
    ip = ipaddress.IPv4Address(address)
    if ip not in ipaddress.IPv4Network('10.77.0.0/24') or int(str(ip).split('.')[-1]) not in range(2, 255):
        raise ValueError('Peer address must be 10.77.0.2 through 10.77.0.254')
    if name in peers or any(peer['address'] == str(ip) for peer in peers.values()):
        raise ValueError('Peer name or address already exists; revoke it deliberately before replacing')


def server_config(private_key, peers):
    text = '[Interface]\nPrivateKey = ' + private_key + '\nListenPort = 51820\n'
    for name, peer in sorted(peers.items()):
        text += f'\n# {name}\n[Peer]\nPublicKey = {peer["public_key"]}\nPresharedKey = {peer["preshared_key"]}\nAllowedIPs = {peer["address"]}/32\n'
    return text


def client_config(private_key, public_key, preshared_key, address):
    return f'''[Interface]
PrivateKey = {private_key}
Address = {address}/32
DNS = 10.77.0.1

[Peer]
PublicKey = {public_key}
PresharedKey = {preshared_key}
Endpoint = 2.29.4.98:51820
AllowedIPs = 10.77.0.1/32
PersistentKeepalive = 25
'''


def apply(private_key, peers):
    raw = server_config(private_key, peers)
    private_write(STATE / 'runtime.conf', raw)
    private_write(CONFIG, raw.replace('[Interface]\n', '[Interface]\nAddress = 10.77.0.1/24\n', 1))
    private_write(STATE / 'peers.json', json.dumps(peers, indent=2) + '\n')
    if 'wgmonitor' in command('wg', 'show', 'interfaces').split():
        subprocess.run(['wg', 'syncconf', 'wgmonitor', str(STATE / 'runtime.conf')], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('init')
    add = sub.add_parser('add-peer')
    add.add_argument('name')
    add.add_argument('address')
    revoke = sub.add_parser('revoke-peer')
    revoke.add_argument('name')
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0:
        raise SystemExit('Run on the production host as root')
    if CONFIG.exists() and not (STATE / 'server.key').exists():
        raise SystemExit('Refusing to replace an existing unmanaged WireGuard configuration')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    key_file = STATE / 'server.key'
    if not key_file.exists():
        private_write(key_file, command('wg', 'genkey') + '\n')
    private_key = key_file.read_text().strip()
    public_key = command('wg', 'pubkey', data=private_key + '\n')
    peers_file = STATE / 'peers.json'
    peers = json.loads(peers_file.read_text()) if peers_file.exists() else {}
    if args.action == 'add-peer':
        validate_peer(args.name, args.address, peers)
        client_key = command('wg', 'genkey')
        psk = command('wg', 'genpsk')
        peers[args.name] = {'address': args.address, 'public_key': command('wg', 'pubkey', data=client_key + '\n'), 'preshared_key': psk}
        profiles = STATE / 'profiles'
        profiles.mkdir(exist_ok=True, mode=0o700)
        profile = profiles / (args.name + '.conf')
        private_write(profile, client_config(client_key, public_key, psk, args.address))
        apply(private_key, peers)
        print('Private client profile written to', profile)
    elif args.action == 'revoke-peer':
        if args.name not in peers:
            raise SystemExit('Unknown peer')
        del peers[args.name]
        apply(private_key, peers)
        (STATE / 'profiles' / (args.name + '.conf')).unlink(missing_ok=True)
        print('Peer revoked:', args.name)
    else:
        apply(private_key, peers)
        print('WireGuard server configuration ready; existing keys and peers preserved.')


if __name__ == '__main__':
    main()
