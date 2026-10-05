# Network management foundation

This module starts the network inventory and workflow service requested for Netwizzard. It uses Python 3.11+ standard library and SQLite. It is a **development foundation**, not a production network controller.

## Run locally

```sh
export NETWIZZARD_DB=./network-manager.sqlite3
python3 app.py bootstrap "Example tenant"
python3 app.py serve
```

Open `http://127.0.0.1:8080/` on the same machine and enter the one-time bootstrap key. The responsive console works on desktop and mobile browsers and includes an installable web-app manifest. The key stays in tab memory; locking or closing the tab clears it. This is a mobile web console, **not** an Android APK or iOS native application. HTTPS and a proper identity provider are needed before phone access outside a trusted local environment.

Save the bootstrap key in a secret manager. The service binds to `127.0.0.1:8080` by default. Do not expose it directly to the Internet. A production reverse proxy must enforce HTTPS, request limits, and appropriate identity controls.

```sh
curl -H "Authorization: Bearer $NETWIZZARD_API_KEY" http://127.0.0.1:8080/devices
curl -X POST -H "Authorization: Bearer $NETWIZZARD_API_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"core-1","address":"192.0.2.10","kind":"router"}' http://127.0.0.1:8080/devices
```

Available collections: `/devices`, `/groups`, `/links`, `/events`, `/jobs`, `/audit`. Assign a group with `POST /devices/{device_id}/groups/{group_id}`. Jobs accept `backup`, `config_review`, `patch_review`, and `remote_session`; approval is `POST /jobs/{id}/approve` by a different admin. **Approval records intent only. No SSH, RDP, SNMP, patch, backup, or configuration action is performed.**

Every record carries a tenant ID. Read and write queries scope by the authenticated principal's tenant. Roles are `viewer`, `operator`, and `admin`; this initial CLI creates an admin only. The database schema anticipates richer user management, but secure principal enrollment and key rotation need implementation before production use.

## Requested capability map

| Requirement | Current state | Next implementation |
| --- | --- | --- |
| Multi-tenant device inventory, upload/manual entry, groups | Tenant-scoped API and group assignment | CSV import with validation, UI, ownership and credential vault |
| Topology and event monitoring | Stored links and events | Discovery agents, polling, event correlation and notifications |
| Scheduling, remote configuration, deployment, patches | Approval queue only | Protocol adapters, policy engine, dry run, rollback and change windows |
| Direct connections and remote desktop | Approval queue only | Brokered sessions with MFA, recording and expiring grants |
| LDAP and network authentication | Not implemented | LDAP/OIDC provider, group mapping and least-privilege roles |
| Server/device/node health | Not implemented | Agent telemetry, SNMPv3 and metrics retention |
| Web and mobile console | Responsive browser UI and web-app manifest | Native packages, secure authentication and production deployment |

Remote control and mass deployment need explicit allowlists, per-device credentials in a vault, concurrent-operation limits, audit trails, rollback, and tenant boundary tests. The API must never treat uploaded device information as executable commands.
