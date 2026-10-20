# Observability

Self-hosted production monitoring for Papyrus, which includes Grafana dashboards and email alerts,
Prometheus metrics, Loki logs, and Alloy collection. This repository also owns the
shared Caddy HTTPS proxy and WireGuard access.

## Dashboard

Activate your Papyrus WireGuard tunnel, then open
[monitor.papyrus-reader.com](https://monitor.papyrus-reader.com) and sign in.
The four dashboards cover service health, subdomain traffic, server resources,
and database/sync activity. Public access, including the login page, is blocked.

## Runbooks

- [Monitoring](monitoring/README.md): installation, retention, capacity, privacy, and recovery
- [VPN](vpn/README.md): device setup, access revocation, and troubleshooting
- [HTTPS proxy](edge/README.md): routing, certificates, and deployment

## Development

Keep dashboards, alerts, and configuration in Git. Target `development` for changes
and promote to `master` before deploying. Application deployment files belong in
their component repositories.

Run from the repository root:

```sh
python3 tools/check.py
python3 -m unittest discover -s tests
python3 tools/render.py && git diff --exit-code
python3 tools/smoke.py
sh tools/vpn_smoke.sh
```
