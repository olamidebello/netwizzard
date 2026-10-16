# Session VPN zones

The **Session VPN** tab supports two explicit modes:

- **My computer's VPN:** the browser uses the user's existing computer VPN to reach Netwizzard. No local VPN credentials, configuration or OS routes are read or modified. Server-side management traffic cannot travel through a browser's local VPN.
- **Platform VPN zone:** an assigned tenant user can open a short-lived capability to run the tab's TCP connectivity checks through a server-managed WireGuard zone. Users select an existing inventory device and allowed TCP port; a successful HTTP CONNECT means the proxy opened a connection to that device. No credentials or application payload are sent. Hostname targets, loopback/link-local/multicast/reserved addresses, foreign-tenant devices and destinations outside the zone's network/port allowlists are rejected. Unreachable proxies/tunnels produce an error, never a direct connection.

Only these interactive device connectivity checks use the selected zone. Existing scheduled Ansible jobs, telemetry ingestion, application API calls and other management operations keep their existing routing. This is not a full-computer VPN client, arbitrary web proxy or live SSH session. Do not assume those operations inherit the tab's selected zone.

Session IDs live in tab memory and require the same authenticated user/token that created them. Heartbeat renews the lease to 90 seconds, up to one hour from connection. Disconnect and sign-out revoke it; tab close sends a best-effort disconnect. If the browser cannot send that request, the lease expires. Access revocation or zone withdrawal prevents later checks. Checks have bounded socket timeouts and immediately close their TCP connection. Five sessions per principal are allowed. Expired rows are removed during subsequent session API requests. Audit stores session starts/stops and device-check metadata, not private keys or device responses.

## Provision real zones

Run on the Netwizzard Debian host with a working WireGuard-capable kernel and root access. The playbook installs the latest checked-out application, WireGuard tools, iproute2, nftables and Tinyproxy. It does not invent a VPN provider, remote peer or private key. Obtain a valid client configuration from your own VPN/network administrator first.

1. Pull the latest GitHub main into `/opt/netwizzard`.
2. Copy `zones.example.yml` to `/root/netwizzard-vpn.yml` and define your zones. Each needs a unique ID/index, tenant IDs, an IPv4 tunnel address, allowed destination networks/ports, and the absolute path to a real WireGuard configuration. Keep secrets outside Git. With Ansible local mode, the source configuration is a file on this server. With remote inventory, copy uses the control node's file.
3. The peer must route the configured target networks and have the necessary return routing/NAT. Its `AllowedIPs` must include the allowed target networks (or `0.0.0.0/0`). The tunnel address must match the remote peer's configuration. `wg-quick strip` is used only to extract WireGuard settings; PostUp/PostDown hooks and wg-quick's host-routing changes are not executed. An endpoint hostname is resolved while still in the host namespace before the interface moves. No DNS is needed for numeric inventory checks. IPv6 is not supported by this implementation.
4. Run:

```bash
cd /opt/netwizzard
apt-get update
apt-get install -y ansible-core
ansible-playbook -i localhost, -c local deployment/vpn/install.yml \
  -e @/root/netwizzard-vpn.yml
```

For encrypted variables/configuration add the appropriate `--ask-vault-pass` or vault ID. Do not put plaintext peer secrets on the command line. The supplied example is intentionally empty and cannot create a working VPN.

The gateway reserves `169.254.240.0/24` for its host/namespace links; choose indexes 1–50 and verify this range is unused on your host. Each zone has a dedicated WireGuard network namespace, host veth link, namespace firewall and CONNECT-only application usage. Encrypted WireGuard packets use the host namespace, while destination traffic has a default route only through WireGuard. The namespace firewall allows output through WireGuard and proxy replies to its host veth; it provides no direct Internet route when the VPN is down. The proxy accepts requests only from its host-side veth address. No host default route, forwarding setting, existing site or host firewall rules are replaced. A privileged host operator can access the gateways; they are not intended as a security boundary against root or other untrusted server processes.

The playbook writes private peer files under root-only `/etc/netwizzard/vpn/`; a separate root-owned, group-readable `/etc/netwizzard/vpn-zones.json` contains only non-secret zone metadata and tenant assignments. The Netwizzard service runs unprivileged and reads that inventory. Users see assigned enabled zones; super admins can inspect the non-secret zone inventory in the platform dashboard. Zone configuration changes remain server-side Ansible operations.

## Operations

```bash
systemctl status netwizzard-zone-office netwizzard-vpn-office
ip netns exec nw-office wg show
journalctl -u netwizzard-vpn-office --no-pager -n 30
```

Use your actual zone ID instead of `office`. Do not share WireGuard private configuration or secret command output. To revoke access, remove a tenant ID or set `enabled: false` and rerun the playbook. Keep disabled entries in the configuration to stop their services; removing an entry alone does not uninstall its old units. Restarting a zone interrupts current probes. Physical zone tunnels are shared server infrastructure; ending a user session revokes that user's ability to request checks without shutting down other users' zone infrastructure.

## Verification limits

The API suite tests tenant/session boundaries, lease expiration, logout, destination restrictions, revocation and proxy failures. The Ansible YAML and rendered shell scripts are validated separately. Real tunnel connectivity still requires an operator-supplied peer and testing on the deployment host. A successful playbook service start does not prove the remote VPN peer can route your target subnet; use **Test through zone** for that check.

The namespace design follows WireGuard's documentation at https://www.wireguard.com/netns/ and Tinyproxy's CONNECT functionality at https://tinyproxy.github.io/.
