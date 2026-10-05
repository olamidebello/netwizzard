# Netwizzard

A Nigerian DID, PBX, contact-center, and softphone platform. This is a new repository; production switching and applications are not implemented yet.

## Implemented network management module

The [`network_manager/`](network_manager/) directory contains a tenant-scoped Python API and responsive web/mobile browser console. It supports isolated tenants with separate platform super-admin and tenant-admin controls, device inventory and ownership, groups, topology links, agent-reported discovery and telemetry, threshold alert policies with deduplicated in-app notifications, events, approved change requests, group deployment queues, recurring schedules, CSV import/export, configuration drafts, and a customizable dashboard. Device vault references are metadata only. The opt-in Ansible worker runs only fixed connectivity-check and Debian-baseline playbooks after a different administrator approves each job; expired worker claims need manual review. See the [network manager setup and user manual](network_manager/README.md) for GUI steps, API usage, limits, and tests.

This code is on a draft branch and is not deployed to a server. Device credentials, HTTPS, secure administrator enrollment, vault integration, MFA, SSH brokering, canary rollout, rollback, native clients, and production safeguards remain prerequisites for real deployments. The telecom switch, DID purchasing, and softphone applications below are still planned.

## Proposed service boundaries

- Kamailio: SIP edge, registration, routing, and protection.
- FreeSWITCH: media, PBX, IVR, queues, conferencing, and recordings.
- PostgreSQL: tenant, DID, rate, routing, CDR, provisioning, and audit data.
- Redis: transient call and job state.
- API and admin UI: tenant-scoped management, RBAC, LDAP group mapping, price controls, reports, and customer provisioning.
- Softphone clients: web, desktop, Android, and iOS, backed by SIP over TLS/WebSocket and SRTP.
- Ansible: reproducible Debian 12 bootstrap for a single node, with separate roles for later clustering.

The target of 500 concurrent calls requires load tests with the actual codecs, recording, transcoding, trunks, and server hardware. It is not a guaranteed capacity from the software choice alone.

## Number inventory

The proposed 203150XXXX–203154XXXX blocks contain 50,000 candidate numbers. They should remain unavailable for purchase until allocation, authority to sell, routing, and carrier interconnect are verified. Store E.164 number, country, allocation reference, upstream carrier, status, tenant, reservation expiry, assignment, and provisioning audit history.

## Milestones

1. Debian 12 Ansible bootstrap, CI, pinned dependencies, secrets handling, health checks, backup and restore.
2. SIP registration and end-to-end calls on a test trunk.
3. Multi-tenant API, RBAC, LDAP mapping, audit trail, and administrative UI.
4. DID inventory, purchase/reservation, assignment, and provisioning workflow.
5. Versioned buy/sell tariffs with manual, percentage, and absolute adjustments.
6. PBX and contact-center workflows, CDR ingestion, billing, and analytics.
7. Branded web, desktop, Android, and iOS softphones and download distribution.
8. Nigerian clearing-house/operator interconnect and authorized NIN verification based on actual provider specifications.

## Production inputs

Debian 12 server access, DNS control for `sip.dobhrap.com`, TLS certificate strategy, trunk/interconnect specifications, DID allocation evidence, and authorized NIN provider documentation. Never place credentials, private keys, or identity data in git.
