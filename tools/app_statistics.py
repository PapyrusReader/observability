#!/usr/bin/env python3
"""Publish aggregate app statistics without exporting account or book records."""
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path('/var/lib/papyrus-observability/textfile/app-statistics.prom')
FORMATS = {
    'book_file': ('epub', 'pdf', 'mobi', 'azw3', 'txt', 'cbr', 'cbz', 'other'),
    'cover_image': ('jpg', 'jpeg', 'png', 'webp', 'gif', 'other'),
    'other': ('other',),
}


def atomic_write(path, text):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(text)
    temporary.chmod(0o644)
    temporary.replace(path)


def query(command=None):
    if command is None:
        command = ['docker', 'exec', '-i', 'papyrus-production-database-1', 'sh', '-c',
                   'psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
    result = subprocess.run(command, input=(ROOT / 'monitoring/app-statistics.sql').read_text(),
                            capture_output=True, text=True, check=True, timeout=15)
    return json.loads(result.stdout)


def number(value):
    if type(value) is not int or value < 0:
        raise ValueError('Expected a nonnegative aggregate integer')
    return value


def render(snapshot):
    lines = []

    def gauge(name, help_text, samples):
        name = 'papyrus_app_' + name
        lines.extend([f'# HELP {name} {help_text}', f'# TYPE {name} gauge'])
        lines.extend(f'{name}{labels} {number(value)}' for labels, value in samples)

    gauge('users', 'Current registered accounts by enabled or disabled status.',
          [(f'{{status="{status}"}}', snapshot['users'][status]) for status in ('enabled', 'disabled')])
    gauge('recent_registrations', 'Accounts still present that were created within the trailing window.',
          [(f'{{window="{window}"}}', snapshot['registrations'][window]) for window in ('24h', '7d', '30d')])
    gauge('books', 'Current synced library entries including physical and metadata-only books.',
          [(f'{{kind="{kind}"}}', snapshot['books'][kind]) for kind in ('digital', 'physical')])
    gauge('distinct_uploaded_contents', 'Distinct valid SHA-256 contents among uploaded book files.',
          [('', snapshot['distinct_uploaded_contents'])])
    buckets = {(kind, format): (0, 0) for kind, formats in FORMATS.items() for format in formats}
    seen = set()
    for row in snapshot['files']:
        key = (row['kind'], row['format'])
        if key not in buckets or key in seen:
            raise ValueError('Unexpected or duplicate aggregate bucket')
        seen.add(key)
        buckets[key] = (number(row['files']), number(row['bytes']))
    for index, name, description in [(0, 'files', 'Current uploaded media records by kind and format.'),
                                     (1, 'file_bytes', 'Logical bytes of current uploaded media records, including duplicate copies.')]:
        gauge(name, description, [(f'{{kind="{kind}",format="{format}"}}', values[index])
                                  for (kind, format), values in buckets.items()])
    return '\n'.join(lines) + '\n'


def collect(output=OUTPUT, fetch=query):
    started = time.monotonic()
    output.parent.mkdir(parents=True, exist_ok=True)
    previous = output.read_text() if output.exists() else ''
    previous = '\n'.join(line for line in previous.splitlines()
                         if 'papyrus_app_collection_success' not in line
                         and 'papyrus_app_collection_duration_seconds' not in line) + '\n'
    try:
        text = render(fetch())
        text += '# HELP papyrus_app_statistics_timestamp_seconds Last successful app statistics snapshot.\n'
        text += '# TYPE papyrus_app_statistics_timestamp_seconds gauge\n'
        text += f'papyrus_app_statistics_timestamp_seconds {time.time()}\n'
        success = True
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
        text = previous
        success = False
    text += '# HELP papyrus_app_collection_success Whether the last collection attempt succeeded.\n'
    text += '# TYPE papyrus_app_collection_success gauge\n'
    text += f'papyrus_app_collection_success {int(success)}\n'
    text += '# HELP papyrus_app_collection_duration_seconds Duration of the last collection attempt.\n'
    text += '# TYPE papyrus_app_collection_duration_seconds gauge\n'
    text += f'papyrus_app_collection_duration_seconds {time.monotonic() - started}\n'
    atomic_write(output, text)
    if not success:
        raise RuntimeError('App statistics collection failed. Previous snapshot preserved, if available.')


if __name__ == '__main__':
    try:
        collect()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
