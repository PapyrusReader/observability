# Papyrus observability

Self-hosted Grafana, Prometheus, Loki, and Alloy for the Papyrus production VM.
Configuration, dashboards, alerts, and the shared Caddy HTTPS proxy live here.
Application deployment files remain in their component repositories.

## Operating model

- `development` integrates changes. Promote to `master` with a merge commit and
  deploy a reviewed revision explicitly. No deployment occurs from development.
- `edge/` installs into the existing `/srv/apps/papyrus-edge` directory and keeps
  the `papyrus-edge` project, network, and certificate volumes.
- This repository installs at `/srv/apps/papyrus-observability`. Its independent
  Compose project is `papyrus-observability`, with persistent named volumes.
- Grafana is at `https://monitor.papyrus-reader.com`, accessible only through
  WireGuard. Login is still required. See [VPN setup and recovery](vpn/README.md).
  The other monitoring services have no public ports.
- The four dashboards cover overview, traffic, resources, and database/sync.
  Grafana manages email alerts. Dashboard and alert changes belong in Git.

## Install

First validate with `python3 tools/check.py`, `python3 -m unittest discover -s tests`,
`python3 tools/render.py` (must leave no diff), and `python3 tools/smoke.py`.
The smoke test uses isolated networks, volumes, fake credentials and localhost ports.
It does not access production databases or send email. The production collector
uses Docker's containerd image store, its `moby` namespace, and the host
`/run/containerd/containerd.sock`. Linux collector acceptance
must additionally run on the actual Linux host, not the Docker Desktop guest.

1. Deploy the server repository's PowerSync metrics configuration on port 9090.
   Restart only PowerSync and check `/probes/readiness`. Do not publish that port.
2. Install the reviewed master revision at `/srv/apps/papyrus-observability`.
3. As root run `python3 tools/bootstrap.py --recipient YOUR_ALERT_ADDRESS`.
   It reads existing SMTP settings locally and provisions `papyrus_monitor` roles
   with `pg_monitor`, no application-table grants, and random separate passwords.
   Generated secrets stay in `monitoring/secrets/` under a root-only directory.
   The bind-mounted secret files are readable by their container users. Do not
   print them or use secrets in shell arguments. Existing secrets are preserved.
4. Run `sh monitoring/deploy.sh`. No application stack is recreated by this command.
5. Install the units in `systemd/` into `/etc/systemd/system`, then run
   `systemctl daemon-reload` and `systemctl enable --now papyrus-observability.timer`.
   Run `systemctl start papyrus-observability.service` for initial measurements.
6. Install and verify WireGuard using `vpn/README.md`. Copy the new edge Caddyfile into the existing bind-mounted file, preserving its
   inode. Validate and reload using the commands in `edge/README.md`. The proxy's
   Compose name, volumes and production directory must not change.
7. Verify DNS, TLS, authenticated dashboards, private endpoint isolation, alert
   firing and recovery email delivery, and collection correctness. Retrieve the
   initial password privately from `monitoring/secrets/grafana_admin_password`.

A narrow `docker compose --env-file production.env restart powersync`
from the existing app deployment directory applies PowerSync's configuration.
Do not run the application's full deployment script just to enable monitoring.

## Capacity and privacy

Prometheus retains up to 90 days or 3 GB of blocks, whichever expires first.
The head and WAL consume additional space. Alloy's metrics WAL retains at most
one hour. Loki retains 14 days, with bounded ingestion and query concurrency.
Grafana is capped at 768 MiB and 0.5 CPU, with a 512 MiB Go memory target.
The other services are capped at 512 MiB and 0.25 CPU each.
Docker continues to rotate local logs at 10 MB times three files.
These limits must be checked under representative load.

The host collector stops Loki at 6 GiB of Loki data or less than 8 GiB free root
space. Metrics remain available. Log delivery retries are bounded and eventually
drop logs rather than buffer indefinitely. Alert thresholds start earlier.
The guard marker persists across deployments and requires deliberate recovery.

Caddy excludes URLs, client addresses, and headers from access logs. Alloy
redacts known query, token, address, email, and user/media identifier patterns in
application logs. This is defense in depth, not a guarantee against arbitrary
sensitive free-form messages. Application code must still avoid logging secrets
and book content. Loki labels contain only project and service identity.
Alloy has privileged host and Docker access and must stay private.

HTTP bytes are response payload measurements, not billed network traffic.
Request counts are not visitor counts. Sync latency represents completed streams,
not ordinary page latency. Use the PowerSync connection metrics alongside it.
Host-origin probes cannot report a whole-host outage. External uptime monitoring,
tracing, visitor analytics, and historical telemetry backups are deferred.
Provisioned configuration is recoverable from Git; local historical data is not
recoverable if the VM is lost. The existing public status page is unchanged.

## Recovery and rollback

For a disk guard event, inspect `df -h` and volume measurements in Grafana. Resolve
capacity pressure or reduce retention through reviewed configuration. Do not
remove application volumes or blindly delete database, Loki, or Prometheus files.
After capacity is safe, run `python3 tools/host_metrics.py --resume`, then
`sh monitoring/deploy.sh`. The command refuses recovery while the thresholds fail.

To roll back, disable the host timer and run `docker compose --env-file
monitoring/monitoring.env -f monitoring/compose.yml stop` from this repository.
Restore and reload the previous edge configuration and restore the previous
PowerSync YAML if necessary. Keep all named volumes and monitoring roles for a
subsequent restart. Never use `down -v` in production.

Acceptance requires a 24-hour observation window with no monitoring OOM events,
no missing required collections, at least 512 MiB available memory, and usable
dashboards. Record measured ingestion and disk growth before increasing retention.

Once all required scrapes are healthy, record the Unix timestamp in
`/var/lib/papyrus-observability/observation-started` and enable
`papyrus-observability-acceptance.timer`. It evaluates the full-day window after
24 hours and five minutes, writes `/var/lib/papyrus-observability/acceptance.json`,
and exports its result through the host textfile collector. The report checks
sample coverage, available memory, OOMs, scrape health, collector freshness, and
the log guard, and records ingestion and storage trends. A failure needs human
review; it never changes resource limits or restarts the application.
