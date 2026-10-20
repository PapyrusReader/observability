#!/bin/sh
set -eu
cd /srv/apps/papyrus-observability
command -v wg >/dev/null
command -v dnsmasq >/dev/null
ufw status | head -1 | grep -qx 'Status: active'
python3 vpn/manage.py init
install -m 644 systemd/papyrus-vpn-firewall.service systemd/papyrus-vpn-dns.service /etc/systemd/system/
install -d /etc/systemd/system/wg-quick@wgmonitor.service.d /etc/systemd/system/docker.service.d
install -m 644 vpn/wg-quick-override.conf /etc/systemd/system/wg-quick@wgmonitor.service.d/papyrus.conf
install -m 644 vpn/docker-override.conf /etc/systemd/system/docker.service.d/papyrus-vpn.conf
# Insert the DNS exceptions above the interface-wide deny. UFW preserves these
# rules across reloads. Other host services, including SSH, stay outside the VPN.
ufw insert 1 deny in on wgmonitor comment 'Papyrus VPN host isolation'
ufw insert 1 allow in on wgmonitor to 10.77.0.1 port 53 proto tcp comment 'Papyrus VPN DNS'
ufw insert 1 allow in on wgmonitor to 10.77.0.1 port 53 proto udp comment 'Papyrus VPN DNS'
ufw allow in on eth0 to 2.29.4.98 port 51820 proto udp comment 'Papyrus WireGuard'
systemctl daemon-reload
systemctl enable --now papyrus-vpn-firewall.service
systemctl reload papyrus-vpn-firewall.service
systemctl enable --now wg-quick@wgmonitor.service papyrus-vpn-dns.service
echo 'WireGuard and private DNS started. Verify a client before reloading the VPN-only Caddy route.'
