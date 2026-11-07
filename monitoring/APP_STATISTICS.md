# App statistics

The **Papyrus / App statistics** dashboard shows server-side aggregates every five
minutes. Guest libraries and device-only books or files are outside its scope.

| Measurement | Definition |
| --- | --- |
| Registered users | Current `users` rows, split into enabled and disabled accounts. Enabled does not mean active. |
| Recent registrations | Accounts still present that were created in the last 24 hours, 7 days, or 30 days. Deleted accounts are excluded. |
| Library entries | Current `books` rows, including physical and metadata-only books. Copies across libraries count separately. |
| Distinct uploaded book contents | Distinct valid SHA-256 checksums in uploaded book files. Different formats or editions may count separately. |
| Uploaded files and bytes | Current `media_assets` rows and their recorded sizes, grouped by kind and a fixed format allowlist. Duplicate copies consume bytes separately. |
| Media disk usage | Existing host measurement of the media volume, including filesystem allocation and unregistered or temporary files. Refreshed every ten minutes. |

Counts and bytes are gauges: deletions can reduce them. Historical charts start
at collection, not at the app's launch. No user IDs, emails, titles, filenames,
paths, or checksums are exported. Unknown formats map to `other`.

## Deployment

From the released checkout at `/srv/apps/papyrus-observability`:

```sh
install -m 644 systemd/papyrus-app-statistics.service systemd/papyrus-app-statistics.timer /etc/systemd/system/
systemctl daemon-reload
systemctl start papyrus-app-statistics.service
systemctl enable --now papyrus-app-statistics.timer
```

The separate host job executes the fixed aggregate SQL through the existing local
database container credentials. It uses a read-only transaction, a five-second
statement timeout, a one-second lock timeout, and a 64 MiB temporary-file limit.
Query parallelism is disabled. No application schema changes,
new grants, credentials, public endpoint, or app restart are needed. Only this
already privileged host job accesses application tables. Grafana and Alloy receive
aggregate textfile metrics through the existing collector.

Provision the dashboard and reload Grafana's alerting configuration after the first
successful sample. Verify the five-minute timer, sample age, all dashboard queries,
and the existing public service probes. The stale-collection alert fires when the
last successful snapshot is over fifteen minutes old, or its metric is missing.

## Failures and recovery

A failed query preserves the last successful counts and timestamp, sets collection
success to zero, and exits unsuccessfully. It never replaces unavailable data with
zero counts. The next timer run retries. Inspect `systemctl status
papyrus-app-statistics.service` and its journal, then run the service again after
fixing the cause. Query errors and database rows are not printed.

Rollback: disable `papyrus-app-statistics.timer`, remove this dashboard and its
stale-collection rule through reviewed configuration, reload Grafana, and remove
only `/var/lib/papyrus-observability/textfile/app-statistics.prom`. Existing metrics,
logs, application data, and media remain untouched.
