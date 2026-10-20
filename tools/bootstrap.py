#!/usr/bin/env python3
"""Create monitoring-only credentials on the host without disclosing app secrets."""
import argparse
import os
import secrets
import subprocess
from pathlib import Path
from urllib.parse import quote


def read_env(path):
    result = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('\"\x27')
    return result


def bootstrap(app_env, destination, recipient):
    values = read_env(app_env)
    destination.mkdir(parents=True, exist_ok=True)
    secret_dir = destination / 'secrets'
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    def write_secret(name, value):
        path = secret_dir / name
        if not path.exists():
            path.write_text(value)
            path.chmod(0o644)
        return path.read_text()
    write_secret('grafana_admin_password', secrets.token_urlsafe(32))
    write_secret('grafana_secret_key', secrets.token_urlsafe(48))
    write_secret('smtp_password', values['SMTP_PASSWORD'])
    for service, db_key, secret_name in [('database', 'POSTGRES_DB', 'postgres_dsn'), ('powersync-storage', 'POWERSYNC_STORAGE_DB', 'powersync_storage_dsn')]:
        database = values[db_key]
        password = secrets.token_hex(32)
        dsn_path = secret_dir / secret_name
        if dsn_path.exists():
            continue
        sql = f"""\\set ON_ERROR_STOP on
SELECT 'CREATE ROLE papyrus_monitor LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='papyrus_monitor')
\\gexec
ALTER ROLE papyrus_monitor WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{password}';
GRANT pg_monitor TO papyrus_monitor;
SELECT format('GRANT CONNECT ON DATABASE %I TO papyrus_monitor', current_database())
\\gexec
"""
        command = ['docker', 'exec', '-i', 'papyrus-production-' + service + '-1', 'sh', '-c', 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
        subprocess.run(command, input=sql, text=True, check=True, stdout=subprocess.DEVNULL)
        write_secret(secret_name, f'postgresql://papyrus_monitor:{password}@{service}:5432/{quote(database, safe="")}?sslmode=disable')
    env = {
        'GRAFANA_DOMAIN': 'monitor.papyrus-reader.com', 'ALERT_EMAIL': recipient,
        'SMTP_HOST': values['SMTP_HOST'] + ':' + values.get('SMTP_PORT', '587'),
        'SMTP_USERNAME': values['SMTP_USERNAME'], 'SMTP_FROM_EMAIL': values['SMTP_FROM_EMAIL'],
        'SMTP_STARTTLS_POLICY': 'MandatoryStartTLS' if values.get('SMTP_USE_TLS', 'true').lower() == 'true' else 'NoStartTLS',
    }
    target = destination / 'monitoring.env'
    target.write_text(''.join(key + "='" + value.replace('\\', '\\\\').replace("'", "\\'") + "'\n" for key, value in env.items()))
    target.chmod(0o600)
    print('Monitoring credentials configured; no credential values printed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-env', type=Path, default=Path('/srv/apps/papyrus/deploy/production.env'))
    parser.add_argument('--destination', type=Path, default=Path(__file__).resolve().parents[1] / 'monitoring')
    parser.add_argument('--recipient', required=True)
    args = parser.parse_args()
    bootstrap(args.app_env, args.destination, args.recipient)
