#!/usr/bin/env python3
"""Exercise real provisioning, PromQL, log redaction and persistence in isolation."""
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from app_statistics import collect
from render import PUBLIC_HOSTS

ROOT = Path(__file__).resolve().parents[1]


def request(url, payload=None, auth=False, headers=None):
    headers = dict(headers or {})
    if auth:
        headers['Authorization'] = 'Basic ' + base64.b64encode(b'admin:smoke-password').decode()
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    with urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers), timeout=10) as response:
        data = response.read().decode()
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return data


def retry(fn, seconds=90):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return fn()
        except (AssertionError, OSError, urllib.error.URLError, KeyError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)


def smoke():
    images = json.loads((ROOT / 'images.lock.json').read_text())
    with tempfile.TemporaryDirectory(prefix='papyrus-observability-') as directory:
        folder = Path(directory)
        shutil.copytree(ROOT / 'monitoring/grafana', folder / 'grafana')
        shutil.copy(ROOT / 'monitoring/loki.yml', folder / 'loki.yml')
        (folder / 'prometheus.yml').write_text('global:\n  scrape_interval: 1s\nscrape_configs:\n  - job_name: self\n    static_configs:\n      - targets: ["prometheus:9090"]\n  - job_name: app-statistics\n    metrics_path: /app-statistics.prom\n    static_configs:\n      - targets: ["caddy:8081"]\n  - job_name: services\n    static_configs:\n      - targets: ["caddy:9180"]\n        labels:\n          service: edge\n')
        statistics = folder / 'metrics/app-statistics.prom'
        collect(statistics, lambda: {'users': {'enabled': 4, 'disabled': 1},
                'registrations': {'24h': 1, '7d': 2, '30d': 3}, 'books': {'digital': 6, 'physical': 1},
                'distinct_uploaded_contents': 2,
                'files': [{'kind': 'book_file', 'format': 'epub', 'files': 2, 'bytes': 1000},
                          {'kind': 'cover_image', 'format': 'jpg', 'files': 1, 'bytes': 100}]})
        with statistics.open('a') as stream:
            stream.write('papyrus_storage_bytes{kind="media"} 4096\n')
        alloy = (ROOT / 'monitoring/config.alloy').read_text()
        sanitizer = alloy[alloy.index('loki.process "sanitize"'):]
        (folder / 'test.alloy').write_text('loki.source.api "test" {\n http { listen_address = "0.0.0.0"\n listen_port = 9091 }\n forward_to = [loki.process.sanitize.receiver]\n}\n' + sanitizer)
        caddy = (ROOT / 'edge/Caddyfile').read_text()
        snippet = caddy[caddy.index('(access_log)'):caddy.index(':9180')]
        (folder / 'Caddyfile').write_text('''{
 metrics {
  per_host
 }
}
''' + snippet + ''':9180 {
 metrics /metrics
}
:8081 {
 header Content-Type "text/plain; version=0.0.4"
 root * /srv/fixtures
 file_server
}
http://papyrus-reader.com:8080 {
 import access_log
 route {
  import private_metrics
  handle /vpn-test* {
   import vpn_only
   respond "vpn-ok"
  }
  handle /fixture-error {
   respond "fixture failure" 500
  }
  handle {
   respond "ok"
  }
 }
}
''')
        config = {'name': 'papyrus-observability-smoke', 'services': {
            'grafana': {'image':images['grafana'],'environment':{'GF_SECURITY_ADMIN_PASSWORD':'smoke-password','GF_USERS_ALLOW_SIGN_UP':'false','GF_AUTH_ANONYMOUS_ENABLED':'false','GF_ANALYTICS_REPORTING_ENABLED':'false','GF_SMTP_ENABLED':'false','ALERT_EMAIL':'smoke@example.invalid'},'ports':['127.0.0.1::3000'],'volumes':['grafana:/var/lib/grafana',str(folder/'grafana/provisioning')+':/etc/grafana/provisioning:ro',str(folder/'grafana/dashboards')+':/var/lib/grafana/dashboards:ro']},
            'prometheus': {'image':images['prometheus'],'command':['--config.file=/etc/prometheus/prometheus.yml','--web.enable-remote-write-receiver'],'ports':['127.0.0.1::9090'],'volumes':[str(folder/'prometheus.yml')+':/etc/prometheus/prometheus.yml:ro','prometheus:/prometheus']},
            'loki': {'image':images['loki'],'command':['-config.file=/etc/loki/config.yml'],'ports':['127.0.0.1::3100'],'volumes':[str(folder/'loki.yml')+':/etc/loki/config.yml:ro','loki:/loki']},
            'alloy': {'image':images['alloy'],'command':['run','--disable-reporting','--storage.path=/var/lib/alloy','/etc/alloy/config.alloy'],'ports':['127.0.0.1::9091'],'volumes':[str(folder/'test.alloy')+':/etc/alloy/config.alloy:ro','alloy:/var/lib/alloy']},
            'caddy': {'image':'caddy:2.10.2-alpine','ports':['127.0.0.1::8080'],'volumes':[str(folder/'Caddyfile')+':/etc/caddy/Caddyfile:ro',str(folder/'metrics')+':/srv/fixtures:ro']},
        }, 'volumes':{name:{} for name in ['grafana','prometheus','loki','alloy']}}
        compose_file = folder / 'compose.json'
        compose_file.write_text(json.dumps(config))
        command = ['docker','compose','-f',str(compose_file)]
        def compose(*args, capture=False):
            return subprocess.run(command+list(args),check=True,text=True,capture_output=capture)
        def address(service, port):
            return 'http://' + compose('port',service,str(port),capture=True).stdout.strip()
        try:
            compose('up','-d','--quiet-pull')
            grafana = address('grafana',3000)
            prometheus = address('prometheus',9090)
            loki = address('loki',3100)
            alloy = address('alloy',9091)
            edge = address('caddy',8080)
            retry(lambda: request(grafana+'/api/health'))
            retry(lambda: request(prometheus+'/-/ready'))
            retry(lambda: request(loki+'/ready'))
            dashboards = request(grafana+'/api/search?type=dash-db',auth=True)
            expected_dashboards = len(list((folder/'grafana/dashboards').glob('*.json')))
            assert len(dashboards) == expected_dashboards, dashboards
            rules = request(grafana+'/api/v1/provisioning/alert-rules',auth=True)
            expected_rules = json.loads((folder/'grafana/provisioning/alerting/rules.yml').read_text())['groups'][0]['rules']
            assert len(rules) == len(expected_rules), len(rules)
            try:
                request(grafana+'/api/search')
                raise AssertionError('Grafana permits anonymous API access')
            except urllib.error.HTTPError as error:
                assert error.code == 401
            edge_headers = {'Host': 'papyrus-reader.com'}
            request(edge+'/', headers=edge_headers)
            try:
                request(edge+'/fixture-error', headers=edge_headers)
            except urllib.error.HTTPError as error:
                assert error.code == 500
            for dashboard in (folder/'grafana/dashboards').glob('*.json'):
                for panel in json.loads(dashboard.read_text())['panels']:
                    if panel['type'] == 'logs':
                        continue
                    for target in panel['targets']:
                        query = target['expr'].replace('$__range','1h').replace('${host:regex}', PUBLIC_HOSTS)
                        def verify_query():
                            result = request(prometheus+'/api/v1/query?'+urllib.parse.urlencode({'query':query}))
                            assert result['status'] == 'success'
                            if dashboard.stem in {'app-statistics', 'live-traffic'}:
                                assert result['data']['result'], (panel['title'], query)
                        retry(verify_query)
                        if dashboard.stem in {'app-statistics', 'live-traffic'}:
                            def verify_grafana_query():
                                payload = {'from': str(int(time.time()*1000)-3600000), 'to': str(int(time.time()*1000)),
                                           'queries': [{**target, 'expr': query, 'datasource': panel['datasource'], 'intervalMs': 1000, 'maxDataPoints': 1000}]}
                                result = request(grafana+'/api/ds/query', payload, auth=True)['results'][target['refId']]
                                assert any(frame.get('data', {}).get('values', [[]])[0] for frame in result.get('frames', [])), panel['title']
                            retry(verify_grafana_query)
            for expression, expected in [('sum(papyrus_app_users)', 5), ('sum(papyrus_app_books)', 7),
                                         ('sum(papyrus_app_files)', 3), ('sum(papyrus_app_file_bytes)', 1100)]:
                result = request(prometheus+'/api/v1/query?'+urllib.parse.urlencode({'query':expression}))
                assert float(result['data']['result'][0]['value'][1]) == expected
            for rule in rules:
                expression = rule['data'][0]['model']['expr']
                assert request(prometheus+'/api/v1/query?'+urllib.parse.urlencode({'query':expression}))['status'] == 'success'
            stamp = str(time.time_ns())
            raw = 'smoke-secret-test GET /books/private-book?token=fixture-query Authorization: Bearer fixture-bearer password=fixture-password user@example.com 192.0.2.10'
            retry(lambda: request(alloy+'/loki/api/v1/push',{'streams':[{'stream':{'project':'papyrus-smoke','service_name':'test'},'values':[[stamp,raw]]}]}))
            def verify_log():
                data = request(loki+'/loki/api/v1/query_range?'+urllib.parse.urlencode({'query':'{project="papyrus-smoke"}','start':str(int(stamp)-1_000_000_000),'end':str(time.time_ns())}))
                lines = [line[1] for stream in data['data']['result'] for line in stream['values']]
                assert lines
                for secret in ['fixture-query','fixture-bearer','fixture-password','user@example.com','192.0.2.10','private-book']:
                    assert all(secret not in line for line in lines), (secret, lines)
                return lines
            retry(verify_log)
            try:
                request(edge+'/metrics', headers=edge_headers)
                raise AssertionError('Grafana metrics route is public')
            except urllib.error.HTTPError as error:
                assert error.code == 404
            request(edge+'/?token=edge-secret', headers=edge_headers)
            for headers in [{}, {'X-Forwarded-For':'10.77.0.2', 'X-Real-IP':'10.77.0.2', 'CF-Connecting-IP':'10.77.0.2'}]:
                try:
                    urllib.request.urlopen(urllib.request.Request(edge+'/vpn-test', headers={**headers, **edge_headers}), timeout=10)
                    raise AssertionError('Public request bypassed the VPN gate')
                except urllib.error.HTTPError as error:
                    assert error.code == 404
            logs = compose('logs','--no-color','caddy',capture=True).stdout
            assert 'edge-secret' not in logs and '"status":200' in logs
            access_logs = compose('logs','--no-color','--no-log-prefix','caddy',capture=True).stdout.splitlines()
            access_logs = [line for line in access_logs if line.startswith('{') and 'http.log.access.' in line]
            assert access_logs
            request(alloy+'/loki/api/v1/push', {'streams': [{'stream': {'project': 'papyrus-edge', 'service_name': 'proxy'},
                    'values': [[str(time.time_ns()+index), line] for index, line in enumerate(access_logs)]}]})
            live = json.loads((folder/'grafana/dashboards/live-traffic.json').read_text())
            log_query = live['panels'][-1]['targets'][0]['expr'].replace('${host:regex}', PUBLIC_HOSTS)
            def verify_access_feed():
                result = request(loki+'/loki/api/v1/query_range?'+urllib.parse.urlencode({'query': log_query, 'limit': 100}))
                lines = [line[1] for stream in result['data']['result'] for line in stream['values']]
                assert any('papyrus-reader.com · 500' in line for line in lines), lines
                assert all('edge-secret' not in line and '127.0.0.1' not in line for line in lines)
            retry(verify_access_feed)
            compose('up','-d','--force-recreate','loki','grafana')
            loki = address('loki',3100)
            grafana = address('grafana',3000)
            retry(lambda: request(loki+'/ready'))
            retry(verify_log)
            retry(lambda: request(grafana+'/api/health'))
            assert len(request(grafana+'/api/search?type=dash-db',auth=True)) == expected_dashboards
            print('PASS: provisioning, private access, PromQL, actual redaction, Caddy sanitization, and persistence.')
        except Exception:
            print(compose('logs','--tail','40','--no-color',capture=True).stdout)
            raise
        finally:
            compose('down','-v','--remove-orphans')


if __name__ == '__main__':
    smoke()
