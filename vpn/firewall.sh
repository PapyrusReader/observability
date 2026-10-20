#!/bin/sh
set -eu
# A private source address is trusted only on the WireGuard interface.
# Build our own chain atomically without flushing Docker or UFW rules.
iptables-restore --wait --noflush <<'EOF'
*filter
:PAPYRUS-VPN - [0:0]
-F PAPYRUS-VPN
-A PAPYRUS-VPN -s 10.77.0.0/24 ! -i wgmonitor -j DROP
-A PAPYRUS-VPN -i wgmonitor -s 10.77.0.0/24 -p tcp -m conntrack --ctorigdst 10.77.0.1 --ctorigdstport 443 --ctdir ORIGINAL -j ACCEPT
-A PAPYRUS-VPN -i wgmonitor -j DROP
-A PAPYRUS-VPN -j RETURN
COMMIT
EOF
iptables -w -C DOCKER-USER -j PAPYRUS-VPN 2>/dev/null ||
  iptables -w -I DOCKER-USER 1 -j PAPYRUS-VPN
