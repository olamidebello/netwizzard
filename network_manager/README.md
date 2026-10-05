# Network management foundation

This module starts the network inventory and workflow service requested for Netwizzard. It uses Python 3.11+ standard library and SQLite. It is a **development foundation**, not a production network controller. No live SSH console, remote desktop, identity provider, credential vault, or network scanner is included.

## Run locally

```sh
export NETWIZZARD_DB=./network-manager.sqlite3
python3 app.py bootstrap "Example tenant"
python3 app.py serve
```

For platform-wide tenant provisioning, run `python3 app.py bootstrap-platform` once with the same database before starting the service. This prints a separate one-time super-admin key. Signing in with it opens the platform tenant console; it can list and create tenants and issue new tenant admin keys. It does not grant a tenant data view. Store it outside the application and do not share it with tenant operators. Existing single-tenant bootstraps remain supported.

Open `http://127.0.0.1:8080/` on the same machine and enter the one-time bootstrap key. The responsive console works on desktop and mobile browsers and includes an installable web-app manifest. The key stays in tab memory; locking or closing the tab clears it. This is a mobile web console, **not** an Android APK or iOS native application. HTTPS and a proper identity provider are needed before phone access outside a trusted local environment.

Save the bootstrap key in a secret manager. The service binds to `127.0.0.1:8080` by default. Do not expose it directly to the Internet. A production reverse proxy must enforce HTTPS, request limits, and appropriate identity controls.

```sh
curl -H "Authorization: Bearer $NETWIZZARD_API_KEY" http://127.0.0.1:8080/devices
curl -X POST -H "Authorization: Bearer $NETWIZZARD_API_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"core-1","address":"192.0.2.10","kind":"router"}' http://127.0.0.1:8080/devices
```

Available collections: `/devices`, `/groups`, `/links`, `/events`, `/jobs`, `/automation_rules`, `/audit`. Assign a group with `POST /devices/{device_id}/groups/{group_id}`. Approval is `POST /jobs/{id}/approve` by a different admin. Review-only jobs (`backup`, `config_review`, `patch_review`, `remote_session`) record intent and never execute device commands. The two Ansible operations require the separate opt-in worker described below.

## Inventory, configuration and access controls

- The Devices tab accepts manual entries and a CSV file with `name,address,kind,notes` headers (up to 5,000 rows and 2 MB). Quoted fields are supported. The backend validates the whole batch and inserts it atomically; duplicate names reject the upload. Search filters the visible inventory. Devices can be edited after import.
- Device groups can be created, renamed, assigned and unassigned in the console. All actions are tenant scoped.
- A manual configuration draft can be saved for a device and loaded later as a new revision. Draft text is stored in SQLite and **is not pushed to the device**. Do not paste credentials or private keys into drafts.
- Tenant admins can create users as admin, operator or viewer and revoke access. A generated API key is returned once; only its SHA-256 hash is stored. Viewers can read; operators can manage inventory and request jobs; admins can manage users, scripts, rules and schedules. The current admin cannot revoke their own account. Key rotation, MFA, OIDC/LDAP and session management remain production work.
- Admins can upload `.yml`, `.yaml` or `.sh` files (up to 128 KB) to the script library. Files receive a SHA-256 digest and can be reviewed in the console. **Uploaded scripts are never handed to the Ansible worker and cannot execute.** Only the two checked-in playbooks can run.

The Dashboard tab shows recent devices, tasks and events. Each user can hide widgets and select a manual, 30-second or one-minute refresh interval; preferences are stored per user in the backend via `GET/POST /preferences`, while the API key remains in tab memory only. Devices, groups, events and jobs have export buttons that fetch tenant-scoped rows from `/exports/{collection}` and download CSV. Spreadsheet formula-like values are prefixed on export to reduce CSV injection risk. Events can be acknowledged or resolved; pending or approved jobs can be cancelled before a worker claims them. The web navigation links Dashboard, Devices, Topology, Events, Change requests, Automation, Users & scripts, and Activity to these API operations.

The Change requests view supports selecting up to 100 eligible pending jobs and approving them in one atomic `POST /jobs/approve-batch` action. Only a different tenant admin can approve, and any invalid job rejects the entire selection. Automation rules can be enabled and disabled from the console. The Activity view shows tenant audit entries and schedule run history. These controls do not bypass the opt-in worker requirement or the fixed playbook allowlist.

## Approved mass deployment and event triggers

The Automation tab can queue `ansible_check` or `ansible_deploy` for every device in one group (up to 100 per request). An admin can create a severity rule, optionally scoped to a group. A matching event queues a job for its device. Every job starts in `pending_approval`; a **different** admin must approve it. The GUI shows status and allows approval. Only allowlisted Ansible operations can run; review-only jobs never execute.

To run approved jobs, install `ansible-core` on a protected control node with SSH access to the managed hosts. Configure a dedicated SSH identity and strict host-key verification outside the repository. After setting `NETWIZZARD_DB` to the same protected database path used by the API, run:

```sh
NETWIZZARD_ANSIBLE_ENABLED=1 python3 worker.py once
# Or keep a supervised worker running:
NETWIZZARD_ANSIBLE_ENABLED=1 python3 worker.py loop
```

The worker starts disabled and processes one approved job at a time, honoring UTC `schedule_at` timestamps. Its only playbooks are `ansible/check.yml` (ping/facts) and `ansible/deploy.yml` (Debian baseline packages, serial one host per invocation). It uses `ansible-playbook` without a shell, strict host-key checking, a temporary single-host JSON inventory, and a 15-minute timeout. The console never accepts arbitrary playbook paths, SSH commands, or inventory variables. A result event records success or failure without storing command output or secrets.

Worker claims carry a 16-minute lease. On restart, an expired running claim becomes `needs_review` instead of being replayed automatically, because the device action may already have happened. An administrator must investigate the device and create a new change request when appropriate. This is a recovery guard, not a distributed worker queue or automatic rollback.

## User manual

1. **Sign in:** Open the local console and enter your one-time API key. The key stays in tab memory. Use **Lock** or close the tab to clear it. A platform super admin sees the tenant administration page and can create a tenant with its first admin, add another admin to an existing tenant, view the platform activity log, and revoke a tenant admin while another active admin remains. Copy each one-time key before hiding it. A tenant administrator creates viewer, operator and admin accounts under **Users & scripts**; give each person their own key. A second administrator is required to approve another person's Ansible job.
2. **Set up inventory:** In **Devices**, enter name, IP/hostname, type, optional owner and optional `vault://` credential reference, then select **Add device**. Admins can use **Ownership and vault reference** to change those fields later. The reference is stored as metadata; no vault is connected and no password should be entered. The owner must be an active user in the tenant. The **Edit** control updates name, address and type. Only admins can change ownership or credential reference metadata.
3. **Import and organize:** Upload a CSV with `name,address,kind,notes` headers, up to 5,000 rows and 2 MB. Create groups and assign devices. Search filters the visible list. Export devices, groups, events or tasks with the corresponding buttons. CSV import is atomic; duplicate names reject the batch.
4. **Record topology and events:** **Topology** stores named links. **Events** lets operators record, acknowledge and resolve events; the search field filters the displayed events. **Telemetry** displays recent agent reports with a device filter, refresh button and CSV export. The dashboard shows recent devices, tasks and events; each user can choose visible widgets and a refresh interval.
5. **Prepare changes:** Save a manual configuration draft under **Devices** or upload a script under **Users & scripts** for review. Neither is executable. In **Change requests**, search tasks by device, action, state or ID; request a review action or schedule it. Review actions only record intent.
6. **Automate a group:** In **Automation**, select a group and one of the two fixed playbooks, optionally set an earliest run time, then queue the request. A different admin must approve each task. An admin can approve several eligible tasks at once. An explicitly enabled worker on a protected host runs approved Ansible tasks. Cancel pending or approved tasks before the worker claims them. Event rules and recurring schedules queue new requests, which still require approval.
7. **Inspect activity:** Use **Activity** for recent audit records and schedule runs. A `needs_review` job means an expired worker claim needs manual investigation before any new attempt.

### Discovery agent setup

An administrator can create and revoke an agent under **Users & scripts → Discovery agents**. Copy the agent key when shown; only its hash is stored. A trusted external collector must send reports; this repository does not scan a subnet, poll SNMP, or run an agent daemon. Example:

```sh
curl -X POST http://127.0.0.1:8080/agent/report \
  -H "Authorization: Bearer $NETWIZZARD_AGENT_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"edge-1","address":"192.0.2.10","kind":"router","status":"up","cpu_percent":14,"memory_percent":32}'
```

The report upserts a tenant device, records a telemetry sample and updates its status. A transition to `down` creates one critical event; repeated down reports do not create another event until status recovers. `GET /telemetry` returns the most recent 500 samples within the authenticated tenant. There is no automated retention cleanup or outbound notification delivery. Keep agent keys on a protected collector and use HTTPS before sending them across a network.

### API and operator reference

Every API request except `/health` and static console assets needs `Authorization: Bearer <key>`. `/agent/report` accepts an active agent key, not a user key. Tenant admins manage `/agents` and `/users`; operators manage inventory and request changes; viewers read. `GET /me` returns the account and tenant. `GET /devices`, `/groups`, `/memberships`, `/links`, `/events`, `/jobs`, `/automation_rules`, `/schedules`, `/schedule_runs`, `/config_drafts`, `/scripts`, `/audit`, and `/telemetry` provide the console data with role checks. Consult the forms above for supported writes. Revoke an agent with `POST /agents/{id}/revoke`. Take protected SQLite backups before upgrades; migrations add columns on startup, but there is no rollback migration.

The platform key uses `GET /platform/me`, `GET/POST /platform/tenants`, `GET/POST /platform/tenants/{id}/admins`, `POST /platform/tenants/{id}/admins/{admin_id}/revoke`, and `GET /platform/audit`. A tenant user key cannot call these endpoints. A platform key cannot use tenant-scoped inventory endpoints. Revoking the last active tenant admin is rejected. Platform super-admin key rotation and revocation, federation, MFA and tenant suspension are not implemented; safeguard the bootstrap key and database accordingly. `GET /exports/telemetry` returns tenant-scoped CSV data in the same column/row JSON envelope as the other export endpoints.

### Recurring scheduler

In the Automation tab, an admin can create a schedule for one device group with a first run time and a repeat interval from five minutes to 30 days. The mobile/web console lists each schedule's next run and offers Pause and Resume. The API exposes `GET/POST /schedules`, `POST /schedules/{id}/pause`, `POST /schedules/{id}/resume`, and read-only `GET /schedule_runs`. Times sent to the API must include a UTC offset; the console converts the user's local selection to UTC.

Each worker cycle atomically processes up to 25 due schedules, queues up to 100 jobs per group, and records one run for each due time. It moves the next run forward from the current time, so an outage does not flood devices with missed intervals. If a group exceeds 100 members, that schedule is paused. An empty group records a zero-job run. Every queued job still needs approval by an admin other than the schedule creator; schedules do not grant standing permission to execute. The worker must remain running under a process supervisor for recurring schedules to fire.

The API key from `bootstrap` belongs to one administrator. Use the Users & scripts tab to create a second admin with a distinct key for two-person approvals. Deliver that one-time key through a secure channel. Do not share the bootstrap key between administrators.

This does not provide production-grade mass rollout yet: add retries with bounds, maintenance windows, canary groups, rollback, vault-backed credentials, signed playbook releases, and device-specific modules. Events can originate from an authenticated user API call or an agent status transition. Verify host ownership and authorization before enabling the worker against any real device.

Every record carries a tenant ID. Read and write queries scope by the authenticated principal's tenant. Roles are `viewer`, `operator`, and `admin`; the CLI creates the first admin and the admin API can enroll more users. Stronger credential storage and key rotation remain production work.

## Requested capability map

| Requirement | Current state | Next implementation |
| --- | --- | --- |
| Multi-tenant inventory and ownership | Separate platform super admin and tenant admins, isolated inventory, manual entry, CSV, search, groups, owner ID, vault reference metadata, drafts | Tenant suspension, key rotation, actual discovery scanner and vault integration |
| Topology and events | Stored links, user events, agent status transitions | Polling, richer correlation and notifications |
| Scheduling and deployment | Group scheduler, severity triggers, separate-admin approval, fixed opt-in Ansible playbooks, expired lease review | Canary rollout, rollback, retries and device-specific modules |
| SSH and remote desktop | Review request only; no live session GUI | Broker, MFA, recording and expiring grants |
| LDAP/OIDC | Local API keys and admin/operator/viewer roles | Provider integration, group mapping and finer permissions |
| Device health | Agent-submitted status, CPU and memory samples | SNMPv3, agent daemon, retention and metrics aggregation |
| Web and mobile | Responsive browser console and web-app manifest | Native packages and production deployment |

Remote control and mass deployment need explicit allowlists, per-device credentials in a vault, concurrent-operation limits, audit trails, rollback, and tenant boundary tests. The API must never treat uploaded device information as executable commands.
