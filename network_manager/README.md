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

### Recurring scheduler

In the Automation tab, an admin can create a schedule for one device group with a first run time and a repeat interval from five minutes to 30 days. The mobile/web console lists each schedule's next run and offers Pause and Resume. The API exposes `GET/POST /schedules`, `POST /schedules/{id}/pause`, `POST /schedules/{id}/resume`, and read-only `GET /schedule_runs`. Times sent to the API must include a UTC offset; the console converts the user's local selection to UTC.

Each worker cycle atomically processes up to 25 due schedules, queues up to 100 jobs per group, and records one run for each due time. It moves the next run forward from the current time, so an outage does not flood devices with missed intervals. If a group exceeds 100 members, that schedule is paused. An empty group records a zero-job run. Every queued job still needs approval by an admin other than the schedule creator; schedules do not grant standing permission to execute. The worker must remain running under a process supervisor for recurring schedules to fire.

The API key from `bootstrap` belongs to one administrator. Use the Users & scripts tab to create a second admin with a distinct key for two-person approvals. Deliver that one-time key through a secure channel. Do not share the bootstrap key between administrators.

This does not provide production-grade mass rollout yet: add durable worker leases and restart recovery, retries with bounds, maintenance windows, canary groups, rollback, vault-backed credentials, signed playbook releases, and device-specific modules. The event source is currently an authenticated API caller, not a live telemetry collector. Verify host ownership and authorization before enabling the worker against any real device.

Every record carries a tenant ID. Read and write queries scope by the authenticated principal's tenant. Roles are `viewer`, `operator`, and `admin`; the CLI creates the first admin and the admin API can enroll more users. Stronger credential storage and key rotation remain production work.

## Requested capability map

| Requirement | Current state | Next implementation |
| --- | --- | --- |
| Multi-tenant device inventory, upload/manual entry, groups | Tenant-scoped manual entry, CSV import, search, groups and drafts | Discovery, ownership and credential vault |
| Topology and event monitoring | Stored links and events | Discovery agents, polling, event correlation and notifications |
| Scheduling and mass deployment | Recurring group scheduler, pause/resume, severity triggers, separate-admin approval, opt-in Ansible checks/Debian baseline | Durable worker, canary rollout, rollback and device-specific modules |
| Direct connections and remote desktop | Approval queue only | Brokered sessions with MFA, recording and expiring grants |
| LDAP and network authentication | Not implemented | LDAP/OIDC provider, group mapping and least-privilege roles |
| Server/device/node health | Not implemented | Agent telemetry, SNMPv3 and metrics retention |
| Web and mobile console | Responsive browser UI and web-app manifest | Native packages, secure authentication and production deployment |

Remote control and mass deployment need explicit allowlists, per-device credentials in a vault, concurrent-operation limits, audit trails, rollback, and tenant boundary tests. The API must never treat uploaded device information as executable commands.
