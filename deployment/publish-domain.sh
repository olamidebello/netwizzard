#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
domain=netwizzard.tech
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
bash "$source_dir/deployment/install.sh"
apt-get install -y nginx certbot
install -d -m 0755 /var/www/netwizzard-acme/.well-known/acme-challenge
config=/etc/nginx/sites-available/netwizzard.tech
enabled=/etc/nginx/sites-enabled/netwizzard.tech
backup=$(mktemp -d)
had_config=0
had_enabled=0
if [[ -e "$config" ]]; then cp -a "$config" "$backup/config"; had_config=1; fi
if [[ -e "$enabled" || -L "$enabled" ]]; then cp -a "$enabled" "$backup/enabled"; had_enabled=1; fi
restore() {
  if [[ $had_config == 1 ]]; then cp -a "$backup/config" "$config"; else rm -f "$config"; fi
  rm -f "$enabled"
  if [[ $had_enabled == 1 ]]; then cp -a "$backup/enabled" "$enabled"; fi
  nginx -t && systemctl reload nginx
}
trap 'restore' ERR
if [[ ! -s /etc/letsencrypt/live/$domain/fullchain.pem ]]; then
  cat > "$config" <<'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name netwizzard.tech;
    location ^~ /.well-known/acme-challenge/ { root /var/www/netwizzard-acme; }
    location / { return 503; }
}
NGINX
  ln -sfn "$config" "$enabled"
  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx
  # The domain's A/AAAA records must reach this server; inbound TCP 80/443 must be open.
  certbot certonly --webroot -w /var/www/netwizzard-acme -d "$domain" --non-interactive --agree-tos --register-unsafely-without-email
fi
cat > "$config" <<'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name netwizzard.tech;
    location ^~ /.well-known/acme-challenge/ { root /var/www/netwizzard-acme; }
    location / { return 301 https://netwizzard.tech$request_uri; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name netwizzard.tech;
    ssl_certificate /etc/letsencrypt/live/netwizzard.tech/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/netwizzard.tech/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 3m;
    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}
NGINX
ln -sfn "$config" "$enabled"
nginx -t
systemctl enable --now nginx
systemctl reload nginx
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/netwizzard-reload <<'HOOK'
#!/bin/sh
nginx -t && systemctl reload nginx
HOOK
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/netwizzard-reload
systemctl enable --now certbot.timer
# A graceful reload returns before the new workers take over. The old worker
# may briefly serve another site's certificate; keep TLS verification enabled.
if ! curl --fail --silent --show-error --connect-timeout 3 --max-time 5 \
    --retry 15 --retry-all-errors --retry-delay 1 --retry-max-time 30 \
    --resolve netwizzard.tech:443:127.0.0.1 https://netwizzard.tech/health; then
  echo 'HTTPS verification still failed after retries. Relevant active Nginx configuration:' >&2
  nginx -T 2>&1 | awk '/^# configuration file / || /^[[:space:]]*(listen|server_name|ssl_certificate|ssl_certificate_key)[[:space:]]/ {print}' >&2 || true
  echo 'Check for duplicate netwizzard.tech server names or address-specific 443 listeners. Restoring previous configuration.' >&2
  restore
  trap - ERR
  exit 1
fi
trap - ERR
rm -rf "$backup"
echo
echo 'Netwizzard is configured at https://netwizzard.tech. Initial platform key: /var/lib/netwizzard/platform-bootstrap.txt (root only).'
