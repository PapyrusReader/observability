# Private Grafana access

WireGuard runs on the host as `wgmonitor`, UDP 51820. Its address is
`10.77.0.1/24`. Each device receives its own key and `/32` peer address.
Clients route only `10.77.0.1/32` into the tunnel. A private DNS resolver on that
address resolves `monitor.papyrus-reader.com` to `10.77.0.1`. Other DNS requests
are forwarded through the host resolver while the VPN is active. Ordinary web
traffic stays on the client's normal connection.

The public DNS-only A record remains `2.29.4.98`. It supports Caddy's existing
certificate issuance and renewal. Public HTTP requests for the monitor hostname
get 404 before reaching Grafana. Caddy checks the actual TCP peer address, not
forwarded headers. Keep the Cloudflare proxy disabled for this hostname.

## Install and verify

From a reviewed, released master checkout at `/srv/apps/papyrus-observability`:

1. Install `wireguard-tools` and `dnsmasq-base` with the OS package manager.
2. Run `sh vpn/install.sh`. Existing WireGuard keys and peers are retained.
   This installs dedicated systemd units and additive firewall rules. It does
   not restart Docker, the application, or the shared HTTPS proxy.
3. Run `python3 vpn/manage.py add-peer mac 10.77.0.2`. A private profile is written
   under `/etc/wireguard/papyrus/profiles/`. Copy it to the device over SSH with
   mode 0600, import it in WireGuard, then remove the server's profile copy.
   The server retains only its own key, peer public keys, and shared keys.
4. Confirm a real external WireGuard handshake, private DNS A=10.77.0.1 with no
   public AAAA fallback, valid HTTPS, and Grafana login before updating Caddy.
5. Copy `edge/Caddyfile` into the existing edge bind mount without changing its
   inode. Validate and reload as documented in `edge/README.md`.
6. Verify external `/login` and `/api/health` return 404, including requests with
   forged `X-Forwarded-For`, `X-Real-IP`, and `CF-Connecting-IP` headers. Through
   WireGuard, login must return 200, unauthenticated dashboard API must return
   401, and `/metrics` must remain 404. Test that SSH and database ports cannot
   be reached through the VPN, even with extra client routes.
7. Restart the VPN and DNS units to verify persistence, then verify all public
   application probes and normal monitoring. The host collector alerts if the
   VPN or DNS systemd unit is inactive.

The Docker forwarding rule checks the original destination address and port,
allowing only TCP 443 to the VPN address. Other forwarded VPN traffic is dropped.
UFW allows DNS only on the VPN interface and denies other host services there.
Only WireGuard's UDP port is newly opened publicly. Firewall rules are restored
at boot and after Docker starts. No masquerading or full Internet routing is added.

## Devices and revocation

Use the official WireGuard application on macOS, iOS, Android, Windows or Linux.
Import the private `.conf` file, activate the tunnel, then open the usual dashboard
URL and sign in to Grafana. Use a distinct profile per device. Profile files grant
VPN access and must remain private. Never paste them into issues or chat.

To remove a device, run `python3 vpn/manage.py revoke-peer mac`. This removes the
peer immediately with `wg syncconf` and persists the change for the next boot.
Issue a new name and unused address for a replacement. Existing profiles cannot
be exported again after their private copy has been removed.

If the browser resolves the public address while connected, check custom browser
DNS-over-HTTPS settings and use the system resolver for this hostname. The public
address intentionally returns 404. Server SSH remains available through the
existing public SSH connection for recovery.

## Recovery

Inspect `systemctl status wg-quick@wgmonitor papyrus-vpn-dns papyrus-vpn-firewall`,
`wg show wgmonitor latest-handshakes`, and `ufw status`. Do not use `wg showconf`
or print files in `/etc/wireguard`, which exposes private keys.

Stopping WireGuard or DNS leaves Grafana closed to the public. Prefer fixing the
VPN or using an SSH tunnel for emergency access. Restoring the previous Caddyfile
reopens the public login and requires an explicit decision. Preserve the Caddy
certificate volumes and all application data. Back up `/etc/wireguard/papyrus`
securely if device keys must survive loss of the VM.
