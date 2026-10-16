#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo or as root.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ -f "$source_dir/network_manager/app.py" ]] || exit 1
apt-get update
apt-get install -y python3 curl
python3 -m unittest discover -s "$source_dir/network_manager/tests"
id netwizzard >/dev/null 2>&1 || useradd --system --home-dir /var/lib/netwizzard --shell /usr/sbin/nologin netwizzard
install -d -o root -g netwizzard -m 0750 /opt/netwizzard-app
install -d -o netwizzard -g netwizzard -m 0700 /var/lib/netwizzard
systemctl stop netwizzard.service 2>/dev/null || true
if [[ -f /var/lib/netwizzard/netwizzard.sqlite3 ]]; then
  python3 - <<'PY'
import sqlite3
from datetime import datetime, timezone
path = '/var/lib/netwizzard/netwizzard.sqlite3'
backup = path + '.' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.bak'
with sqlite3.connect(path) as src, sqlite3.connect(backup) as dst:
    src.backup(dst)
PY
  chmod 600 /var/lib/netwizzard/*.bak
fi
cp -a "$source_dir/network_manager/." /opt/netwizzard-app/
chown -R root:netwizzard /opt/netwizzard-app
chmod -R o-rwx /opt/netwizzard-app
if [[ ! -f /var/lib/netwizzard/netwizzard.sqlite3 ]]; then
  umask 077
  runuser -u netwizzard -- env NETWIZZARD_DB=/var/lib/netwizzard/netwizzard.sqlite3 python3 /opt/netwizzard-app/app.py bootstrap-platform > /var/lib/netwizzard/platform-bootstrap.txt
fi
cat > /etc/systemd/system/netwizzard.service <<'UNIT'
[Unit]
Description=Netwizzard network management API and console
After=network.target

[Service]
User=netwizzard
Group=netwizzard
WorkingDirectory=/opt/netwizzard-app
Environment=NETWIZZARD_DB=/var/lib/netwizzard/netwizzard.sqlite3
Environment=NETWIZZARD_HOST=127.0.0.1
Environment=NETWIZZARD_PORT=18080
ExecStart=/usr/bin/python3 /opt/netwizzard-app/app.py serve
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/netwizzard

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now netwizzard.service
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:18080/health
echo
echo 'Netwizzard is running at 127.0.0.1:18080. Initial platform key: /var/lib/netwizzard/platform-bootstrap.txt (root only).'
