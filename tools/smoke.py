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

ROOT = Path(__file__).resolve().parents[1]


def request(url, payload=None, auth=False):
    headers = {}
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
        (folder / 'prometheus.yml').write_text('global:\n  scrape_interval: 1s\nscrape_configs:\n  - job_name: self\n    static_configs:\n      - targets: ["prometheus:9090"]\n')
        alloy = (ROOT / 'monitoring/config.alloy').read_text()
        sanitizer = alloy[alloy.index('loki.process "sanitize"'):]
        (folder / 'test.alloy').write_text('loki.source.api "test" {\n http { listen_address = "0.0.0.0"\n listen_port = 9091 }\n forward_to = [loki.process.sanitize.receiver]\n}\n' + sanitizer)
        caddy = (ROOT / 'edge/Caddyfile').read_text()
        snippet = caddy[caddy.index('(access_log)'):caddy.index(':9180')]
        (folder / 'Caddyfile').write_text(snippet + ':8080 {\n import access_log\n route {\n import private_metrics\n handle /vpn-test* {\n import vpn_only\n respond "vpn-ok"\n }\n handle {\n respond "ok"\n }\n }\n}\n')
        config = {'name': 'papyrus-observability-smoke', 'services': {
            'grafana': {'image':images['grafana'],'environment':{'GF_SECURITY_ADMIN_PASSWORD':'smoke-password','GF_USERS_ALLOW_SIGN_UP':'false','GF_AUTH_ANONYMOUS_ENABLED':'false','GF_ANALYTICS_REPORTING_ENABLED':'false','GF_SMTP_ENABLED':'false','ALERT_EMAIL':'smoke@example.invalid'},'ports':['127.0.0.1::3000'],'volumes':['grafana:/var/lib/grafana',str(folder/'grafana/provisioning')+':/etc/grafana/provisioning:ro',str(folder/'grafana/dashboards')+':/var/lib/grafana/dashboards:ro']},
            'prometheus': {'image':images['prometheus'],'command':['--config.file=/etc/prometheus/prometheus.yml','--web.enable-remote-write-receiver'],'ports':['127.0.0.1::9090'],'volumes':[str(folder/'prometheus.yml')+':/etc/prometheus/prometheus.yml:ro','prometheus:/prometheus']},
            'loki': {'image':images['loki'],'command':['-config.file=/etc/loki/config.yml'],'ports':['127.0.0.1::3100'],'volumes':[str(folder/'loki.yml')+':/etc/loki/config.yml:ro','loki:/loki']},
            'alloy': {'image':images['alloy'],'command':['run','--disable-reporting','--storage.path=/var/lib/alloy','/etc/alloy/config.alloy'],'ports':['127.0.0.1::9091'],'volumes':[str(folder/'test.alloy')+':/etc/alloy/config.alloy:ro','alloy:/var/lib/alloy']},
            'caddy': {'image':'caddy:2.10.2-alpine','ports':['127.0.0.1::8080'],'volumes':[str(folder/'Caddyfile')+':/etc/caddy/Caddyfile:ro']},
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
            assert len(dashboards) == 4, dashboards
            rules = request(grafana+'/api/v1/provisioning/alert-rules',auth=True)
            expected_rules = json.loads((folder/'grafana/provisioning/alerting/rules.yml').read_text())['groups'][0]['rules']
            assert len(rules) == len(expected_rules), len(rules)
            try:
                request(grafana+'/api/search')
                raise AssertionError('Grafana permits anonymous API access')
            except urllib.error.HTTPError as error:
                assert error.code == 401
            for dashboard in (folder/'grafana/dashboards').glob('*.json'):
                for panel in json.loads(dashboard.read_text())['panels']:
                    if panel['type'] == 'logs':
                        continue
                    query = panel['targets'][0]['expr'].replace('$__range','1h')
                    assert request(prometheus+'/api/v1/query?'+urllib.parse.urlencode({'query':query}))['status'] == 'success'
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
                request(edge+'/metrics')
                raise AssertionError('Grafana metrics route is public')
            except urllib.error.HTTPError as error:
                assert error.code == 404
            request(edge+'/?token=edge-secret')
            for headers in [{}, {'X-Forwarded-For':'10.77.0.2', 'X-Real-IP':'10.77.0.2', 'CF-Connecting-IP':'10.77.0.2'}]:
                try:
                    urllib.request.urlopen(urllib.request.Request(edge+'/vpn-test', headers=headers), timeout=10)
                    raise AssertionError('Public request bypassed the VPN gate')
                except urllib.error.HTTPError as error:
                    assert error.code == 404
            logs = compose('logs','--no-color','caddy',capture=True).stdout
            assert 'edge-secret' not in logs and '"status":200' in logs
            compose('up','-d','--force-recreate','loki','grafana')
            loki = address('loki',3100)
            grafana = address('grafana',3000)
            retry(lambda: request(loki+'/ready'))
            retry(verify_log)
            retry(lambda: request(grafana+'/api/health'))
            assert len(request(grafana+'/api/search?type=dash-db',auth=True)) == 4
            print('PASS: provisioning, private access, PromQL, actual redaction, Caddy sanitization, and persistence.')
        except Exception:
            print(compose('logs','--tail','40','--no-color',capture=True).stdout)
            raise
        finally:
            compose('down','-v','--remove-orphans')


if __name__ == '__main__':
    smoke()
