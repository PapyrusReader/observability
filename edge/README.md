# Shared HTTPS entry point

Run this Compose project independently of both the app services and the public
website. It is the only project that publishes ports 80/443 on the Hetzner VM.
Caddy obtains and renews certificates, then routes by hostname to services on the
external `papyrus-edge` Docker network:

| Hostname | Internal service |
| --- | --- |
| `papyrus-reader.com` | `papyrus-website:8080` |
| `www.papyrus-reader.com` | Redirect to the main website |
| `app.papyrus-reader.com` | `papyrus-app:8080` |
| `api.papyrus-reader.com` | `papyrus-api:8080` |
| `sync.papyrus-reader.com` | `papyrus-sync:8080` |

Install these files at `/srv/apps/papyrus-edge` on the VM. Copy
`edge.env.example` to `edge.env`, set the ACME contact email, and run
`sh deploy.sh`. Keep `edge.env` private with `chmod 600 edge.env`. Configure the
app and website Compose projects separately. Their runbooks describe their
releases. Starting or stopping either project does not restart the shared proxy.
The proxy mounts no app or website content and does not need database credentials.

For a Caddyfile-only change, validate and reload without replacing the container:

```sh
docker compose --env-file edge.env -f compose.yml exec -T proxy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose --env-file edge.env -f compose.yml exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

The persistent `papyrus-edge-caddy-data` and `papyrus-edge-caddy-config` volumes
belong to this project. Do not remove them during an app or website release.
When migrating from the earlier app-owned proxy, stop that proxy, copy its
certificate data into the new data volume, then start this proxy to take over
80/443. Preserve the app database/media volumes and keep the previous proxy
configuration and certificate volumes for rollback.

The public website status page reads a cached snapshot maintained by its own
scheduled monitor. The edge does not enable cross-origin browser health checks.

## Monitoring integration

This configuration is owned by `PapyrusReader/observability`. Keep its deployment
at `/srv/apps/papyrus-edge`. Port 9180 serves metrics only on the Docker network;
there is no published host port or public route. `monitor.<WEBSITE_DOMAIN>` routes
to authenticated Grafana only for actual WireGuard peer addresses in
`10.77.0.0/24`. Public requests, including forged forwarding headers, receive 404.
The private VPN resolver maps this hostname to `10.77.0.1`. Keep the public DNS
record DNS-only for certificate validation. See `../vpn/README.md` before changing
this route. The Caddy admin listener remains on container loopback.
Access logs omit request URLs, addresses, and headers before Docker stores them.
