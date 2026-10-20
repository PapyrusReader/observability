#!/usr/bin/env python3
"""Generate reviewable Grafana JSON from the dashboard and alert definitions."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAD = 'container_label_com_docker_compose_service'


def container_usage(expression):
 return 'label_replace(max by(id) (' + expression + '), "container_id", "$1", "id", ".*docker[-/]([a-f0-9]{64})([.]scope)?") * on(container_id) group_left(project,service) max by(container_id,project,service) (papyrus_container_info)'


DASHBOARDS = {
 'overview': [
  ('Required service health', 'papyrus_container_healthy', 'short'),
  ('Monitoring VPN and DNS', 'papyrus_vpn_service_up', 'short'),
  ('Public probes from production', 'probe_success', 'short'),
  ('Requests per second by host', 'sum by(host) (rate(caddy_http_requests_total[5m]))', 'reqps'),
  ('Server errors per second', 'sum by(host) (rate(caddy_http_requests_total{code=~"5.."}[5m])) or (sum by(host) (rate(caddy_http_requests_total[5m])) * 0)', 'reqps'),
  ('Available host memory', 'node_memory_MemAvailable_bytes', 'bytes'),
  ('Root filesystem available', 'papyrus_root_available_bytes', 'bytes'),
  ('Collector and service scrapes', 'up', 'short'),
  ('Loki disk guard (1 = paused)', 'papyrus_loki_disk_guard_active', 'short'),
 ],
 'traffic': [
  ('Requests per second', 'sum by(host) (rate(caddy_http_requests_total[5m]))', 'reqps'),
  ('HTTP response bytes per second', 'sum by(host) (rate(caddy_http_response_size_bytes_sum[5m]))', 'Bps'),
  ('Responses by hostname and status', 'sum by(host,code) (increase(caddy_http_requests_total[1h]))', 'short'),
  ('P95 request duration, excluding sync', 'histogram_quantile(0.95, sum by(le,host) (rate(caddy_http_request_duration_seconds_bucket{host!="sync.papyrus-reader.com"}[5m])))', 's'),
  ('Completed sync connection duration P95', 'histogram_quantile(0.95, sum by(le) (rate(caddy_http_request_duration_seconds_bucket{host="sync.papyrus-reader.com"}[5m])))', 's'),
  ('HTTP response bytes over selected range', 'sum by(host) (increase(caddy_http_response_size_bytes_sum[$__range]))', 'bytes'),
 ],
 'resources': [
  ('Host CPU utilization', '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))', 'percentunit'),
  ('Host load average', 'node_load1', 'short'),
  ('Available memory', 'node_memory_MemAvailable_bytes', 'bytes'),
  ('Container memory working set', container_usage('container_memory_working_set_bytes{image!=""}'), 'bytes'),
  ('Container CPU cores', container_usage('rate(container_cpu_usage_seconds_total{image!=""}[5m])'), 'short'),
  ('Disk read latency', 'rate(node_disk_read_time_seconds_total{device="sda"}[5m]) / clamp_min(rate(node_disk_reads_completed_total{device="sda"}[5m]), 0.001)', 's'),
  ('Disk write latency', 'rate(node_disk_write_time_seconds_total{device="sda"}[5m]) / clamp_min(rate(node_disk_writes_completed_total{device="sda"}[5m]), 0.001)', 's'),
  ('Storage growth', 'papyrus_storage_bytes', 'bytes'),
  ('Host network receive', 'rate(papyrus_host_network_receive_bytes_total[5m])', 'Bps'),
  ('Host network transmit', 'rate(papyrus_host_network_transmit_bytes_total[5m])', 'Bps'),
  ('Restarts', 'increase(papyrus_container_restarts_total[1h])', 'short'),
  ('OOM events', 'increase(papyrus_container_oom_total[1h])', 'short'),
  ('Filesystem free inodes', 'node_filesystem_files_free{mountpoint="/"}', 'short'),
 ],
 'database-sync': [
  ('Database collector connectivity', 'pg_up', 'short'),
  ('Database size', 'pg_database_size_bytes{datname!~"template.*"}', 'bytes'),
  ('Database connections', 'sum by(instance) (pg_stat_database_numbackends)', 'short'),
  ('Database deadlocks', 'increase(pg_stat_database_deadlocks[1h])', 'short'),
  ('Retained replication WAL', 'papyrus_replication_retained_wal_bytes', 'bytes'),
  ('Replication slot active', 'papyrus_replication_active', 'short'),
  ('PowerSync concurrent connections', '{__name__=~".*concurrent_connections.*",job="services"}', 'short'),
  ('Sync bytes per second', 'rate({__name__=~".*data_synced_bytes(_total)?",job="services"}[5m])', 'Bps'),
  ('Replicated bytes per second', 'rate({__name__=~".*data_replicated_bytes(_total)?",job="services"}[5m])', 'Bps'),
 ],
}
ALERTS = [
 ('probe-failed','Public service probe failed','min(probe_success) < 1','3m','critical'),
 ('vpn-unavailable','Monitoring VPN or private DNS unavailable','min(papyrus_vpn_service_up) < 1','3m','critical'),
 ('container-unhealthy','Required container unhealthy','papyrus_container_healthy < 1','3m','critical'),
 ('collection-down','Metrics scrape failed','up < 1','3m','critical'),
 ('host-collector-stale','Host collector stale','time() - papyrus_host_collector_timestamp_seconds > 180','1m','critical'),
 ('app-statistics-stale','App statistics collection stale','time() - papyrus_app_statistics_timestamp_seconds > 900','1m','warning'),
 ('memory-low','Available host memory below 15%','node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes < 0.15','10m','warning'),
 ('memory-critical','Available host memory below 8%','node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes < 0.08','5m','critical'),
 ('cpu-high','Sustained high CPU','1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) > 0.85','15m','warning'),
 ('disk-low','Root disk below 12 GiB','papyrus_root_available_bytes < 12884901888','5m','warning'),
 ('disk-critical','Root disk below 8 GiB','papyrus_root_available_bytes < 8589934592','1m','critical'),
 ('disk-forecast','Root disk projected full within 7 days','predict_linear(papyrus_root_available_bytes[6h], 604800) < 0','30m','warning'),
 ('restarts','Repeated container restarts','increase(papyrus_container_restarts_total[15m]) >= 3','1m','warning'),
 ('oom','Container OOM detected','increase(papyrus_container_oom_total[10m]) > 0','0s','critical'),
 ('http-errors','Elevated HTTP server errors','(sum by(host) (increase(caddy_http_requests_total{code=~"5.."}[10m])) >= 20) and (sum by(host) (rate(caddy_http_requests_total{code=~"5.."}[10m])) / sum by(host) (rate(caddy_http_requests_total[10m])) > 0.05)','5m','critical'),
 ('db-unreachable','Database monitoring connection failed','pg_up < 1','3m','critical'),
 ('replication-inactive','Logical replication slot inactive','papyrus_replication_active < 1','5m','warning'),
 ('db-connections','Database connections above 80%','sum by(instance) (pg_stat_database_numbackends) / on(instance) pg_settings_max_connections > 0.8','10m','warning'),
 ('wal-retained','Replication retaining over 512 MiB WAL','papyrus_replication_retained_wal_bytes > 536870912','10m','warning'),
 ('tls-expiry','TLS certificate expires within 14 days','probe_ssl_earliest_cert_expiry - time() < 1209600','10m','warning'),
 ('loki-paused','Loki paused by disk guard','papyrus_loki_disk_guard_active > 0','0s','critical'),
]


def app_statistics_dashboard():
 panels = []

 def panel(title, expression, unit, x, y, width=12, height=8, kind='timeseries', legend='', description=''):
  result = {'id': len(panels)+1, 'title': title, 'type': kind,
            'description': description, 'datasource': {'type': 'prometheus', 'uid': 'prometheus'},
            'targets': [{'refId': 'A', 'expr': expression, 'legendFormat': legend, 'instant': kind == 'stat', 'range': kind != 'stat'}],
            'gridPos': {'x': x, 'y': y, 'w': width, 'h': height},
            'fieldConfig': {'defaults': {'unit': unit, 'min': 0}, 'overrides': []}}
  if kind == 'stat':
   result['fieldConfig']['defaults']['color'] = {'mode': 'fixed', 'fixedColor': 'blue'}
   result['options'] = {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                        'colorMode': 'value', 'graphMode': 'none'}
  panels.append(result)
  return result

 panel('Registered users', 'sum(papyrus_app_users)', 'short', 0, 0, 6, 4, 'stat',
       description='Current accounts, including disabled accounts. Deleted accounts are excluded.')
 panel('Library entries', 'sum(papyrus_app_books)', 'short', 6, 0, 6, 4, 'stat',
       description='Synced book records, including physical and metadata-only books. Copies in different libraries count separately.')
 panel('Uploaded files', 'sum(papyrus_app_files)', 'short', 12, 0, 6, 4, 'stat',
       description='Current registered media assets: book files and covers. Device-only files are excluded.')
 panel('Uploaded file bytes', 'sum(papyrus_app_file_bytes)', 'bytes', 18, 0, 6, 4, 'stat',
       description='Logical sizes of registered uploads, including duplicate copies. Not filesystem disk usage.')
 panel('Distinct uploaded book contents', 'papyrus_app_distinct_uploaded_contents', 'short', 0, 4, 8, 4, 'stat',
       description='Distinct valid SHA-256 checksums among uploaded book files. Different formats or editions can count separately. This is not a count of literary works.')
 age = panel('Statistics age', 'time() - papyrus_app_statistics_timestamp_seconds', 's', 8, 4, 8, 4, 'stat',
             description='Time since the last successful collection. Normally under five minutes. Last good values remain visible if collection fails.')
 age['fieldConfig']['defaults']['thresholds'] = {'mode': 'absolute', 'steps': [
     {'color': 'green', 'value': None}, {'color': 'orange', 'value': 600}, {'color': 'red', 'value': 900}]}
 age['fieldConfig']['defaults']['color'] = {'mode': 'thresholds'}
 health = panel('Latest collection', 'papyrus_app_collection_success', 'short', 16, 4, 8, 4, 'stat')
 health['fieldConfig']['defaults']['mappings'] = [{'type': 'value', 'options': {
     '0': {'text': 'Failed', 'color': 'red'}, '1': {'text': 'Successful', 'color': 'green'}}}]
 panel('Registered users over time', 'papyrus_app_users', 'short', 0, 8, legend='{{status}}',
       description='Enabled means the account is not disabled. It does not imply recent activity.')
 panel('Recent registrations', 'papyrus_app_recent_registrations', 'short', 12, 8, legend='Last {{window}}',
       description='Accounts still present that were created in each trailing window. Deleted accounts are excluded. These are gauges, not lifetime signup counters.')
 panel('Library entries over time', 'papyrus_app_books', 'short', 0, 16, legend='{{kind}}')
 panel('Uploaded files by kind', 'sum by(kind) (papyrus_app_files)', 'short', 12, 16, legend='{{kind}}')
 panel('Book files by format', 'sum by(format) (papyrus_app_files{kind="book_file"})', 'short', 0, 24, legend='{{format}}')
 panel('Cover images by format', 'sum by(format) (papyrus_app_files{kind="cover_image"})', 'short', 12, 24, legend='{{format}}')
 panel('Book file storage by format', 'sum by(format) (papyrus_app_file_bytes{kind="book_file"})', 'bytes', 0, 32, legend='{{format}}')
 panel('Cover storage by format', 'sum by(format) (papyrus_app_file_bytes{kind="cover_image"})', 'bytes', 12, 32, legend='{{format}}')
 storage = panel('Uploaded bytes and media disk usage', 'sum(papyrus_app_file_bytes)', 'bytes', 0, 40, legend='Registered file bytes',
                 description='Media disk usage also includes filesystem allocation, in-flight uploads and unregistered files. It is measured separately every ten minutes.')
 storage['targets'].append({'refId': 'B', 'expr': 'papyrus_storage_bytes{kind="media"}', 'legendFormat': 'Media volume disk usage', 'instant': False, 'range': True})
 panel('Average uploaded file size', 'sum(papyrus_app_file_bytes) / clamp_min(sum(papyrus_app_files), 1)', 'bytes', 12, 40,
       legend='Average bytes per file', description='All registered uploads, including covers. Zero when no files are uploaded.')
 return {'uid': 'papyrus-app-statistics', 'title': 'Papyrus / App statistics', 'schemaVersion': 39,
         'version': 1, 'editable': False, 'timezone': 'browser', 'refresh': '1m',
         'time': {'from': 'now-6h', 'to': 'now'}, 'tags': ['Papyrus'], 'panels': panels,
         'description': 'Server-synced data, collected every five minutes. Guest libraries and device-only data are excluded. Trends begin when collection is enabled. Only aggregate counts and bytes are retained.'}


def render():
 for name, definitions in DASHBOARDS.items():
  panels=[]
  for index,(title,expr,unit) in enumerate(definitions):
   panels.append({'id':index+1,'title':title,'type':'timeseries','datasource':{'type':'prometheus','uid':'prometheus'},'targets':[{'refId':'A','expr':expr,'legendFormat':'{{host}} {{code}} {{service}} {{kind}} {{server}} {{instance}} {{device}} {{job}}'}],'gridPos':{'x':(index%2)*12,'y':(index//2)*8,'w':12,'h':8},'fieldConfig':{'defaults':{'unit':unit},'overrides':[]}})
  panels.append({'id':100,'title':'Service logs','type':'logs','datasource':{'type':'loki','uid':'loki'},'targets':[{'refId':'A','expr':'{project=~"papyrus-.+"}'}],'gridPos':{'x':0,'y':((len(definitions)+1)//2)*8,'w':24,'h':10}})
  dashboard={'uid':'papyrus-'+name,'title':'Papyrus / '+name.replace('-',' ').title(),'schemaVersion':39,'version':1,'editable':False,'timezone':'browser','refresh':'30s','time':{'from':'now-6h','to':'now'},'tags':['Papyrus'],'panels':panels,'description':'Production-host probes cannot detect a whole-host outage. HTTP bytes exclude transport overhead. Sync durations represent completed streams. History begins at installation.'}
  (ROOT/'monitoring/grafana/dashboards'/f'{name}.json').write_text(json.dumps(dashboard,indent=2)+'\n')
 (ROOT/'monitoring/grafana/dashboards/app-statistics.json').write_text(json.dumps(app_statistics_dashboard(),indent=2)+'\n')
 rules=[]
 for uid,title,expr,pending,severity in ALERTS:
  if uid != 'http-errors':
   expr = re.sub(r' (>=|>|<) ', r' \1 bool ', expr)
  rules.append({'uid':'papyrus-'+uid,'title':title,'condition':'C','for':pending,'noDataState':'OK' if uid in ['http-errors','wal-retained','replication-inactive','oom','restarts'] else 'Alerting','execErrState':'Alerting','annotations':{'summary':title,'description':'Inspect the Papyrus dashboards and the observability recovery runbook.'},'labels':{'severity':severity},'data':[{'refId':'A','relativeTimeRange':{'from':600,'to':0},'datasourceUid':'prometheus','model':{'refId':'A','expr':expr,'instant':True,'intervalMs':1000,'maxDataPoints':43200}},{'refId':'C','datasourceUid':'__expr__','model':{'refId':'C','type':'threshold','expression':'A','conditions':[{'evaluator':{'type':'gt','params':[0]},'operator':{'type':'and'},'reducer':{'type':'last'},'type':'query','query':{'params':['C']}}]}}]})
 (ROOT/'monitoring/grafana/provisioning/alerting/rules.yml').write_text(json.dumps({'apiVersion':1,'groups':[{'orgId':1,'name':'Papyrus production','folder':'Papyrus','interval':'1m','rules':rules}]},indent=2)+'\n')


if __name__ == '__main__':
 render()
