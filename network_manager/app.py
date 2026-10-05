"""Small, dependency-free management API. No device commands are executed."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).with_name("web")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}

DB = os.environ.get("NETWIZZARD_DB", "netwizzard.sqlite3")
HOST = os.environ.get("NETWIZZARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("NETWIZZARD_PORT", "8080"))
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS tenants(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS principals(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id), name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')), key_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id), name TEXT NOT NULL, UNIQUE(tenant_id,name));
CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id), name TEXT NOT NULL, address TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown', notes TEXT NOT NULL DEFAULT '', UNIQUE(tenant_id,name));
CREATE TABLE IF NOT EXISTS memberships(tenant_id INTEGER NOT NULL, device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE, group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE, PRIMARY KEY(device_id,group_id));
CREATE TABLE IF NOT EXISTS links(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, source_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE, target_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE, label TEXT NOT NULL DEFAULT '', CHECK(source_id <> target_id));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL, severity TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, device_id INTEGER NOT NULL REFERENCES devices(id), operation TEXT NOT NULL, schedule_at TEXT, state TEXT NOT NULL DEFAULT 'pending_approval', requested_by INTEGER NOT NULL REFERENCES principals(id), approved_by INTEGER REFERENCES principals(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, principal_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS automation_rules(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, severity TEXT NOT NULL CHECK(severity IN ('info','warning','critical')), operation TEXT NOT NULL CHECK(operation IN ('ansible_check','ansible_deploy')), group_id INTEGER REFERENCES groups(id), created_by INTEGER NOT NULL REFERENCES principals(id), enabled INTEGER NOT NULL DEFAULT 1, UNIQUE(tenant_id,name));
CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, group_id INTEGER NOT NULL REFERENCES groups(id), operation TEXT NOT NULL CHECK(operation IN ('ansible_check','ansible_deploy')), interval_seconds INTEGER NOT NULL CHECK(interval_seconds BETWEEN 300 AND 2592000), next_run_at TEXT NOT NULL, last_run_at TEXT, enabled INTEGER NOT NULL DEFAULT 1, created_by INTEGER NOT NULL REFERENCES principals(id), UNIQUE(tenant_id,name));
CREATE TABLE IF NOT EXISTS schedule_runs(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, schedule_id INTEGER NOT NULL REFERENCES schedules(id), due_at TEXT NOT NULL, created_at TEXT NOT NULL, job_count INTEGER NOT NULL, UNIQUE(schedule_id,due_at));
CREATE TABLE IF NOT EXISTS config_drafts(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, device_id INTEGER NOT NULL REFERENCES devices(id), content TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES principals(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scripts(id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, content TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'stored', created_by INTEGER NOT NULL REFERENCES principals(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preferences(principal_id INTEGER PRIMARY KEY REFERENCES principals(id), tenant_id INTEGER NOT NULL, widgets TEXT NOT NULL, refresh_seconds INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
"""


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    if "active" not in [row[1] for row in db.execute("PRAGMA table_info(principals)")]:
        db.execute("ALTER TABLE principals ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if "status" not in [row[1] for row in db.execute("PRAGMA table_info(events)")]:
        db.execute("ALTER TABLE events ADD COLUMN status TEXT NOT NULL DEFAULT 'open'")
    return db


def now():
    return datetime.now(timezone.utc).isoformat()


ANSIBLE_OPERATIONS = ("ansible_check", "ansible_deploy")


def queue_job(db, tenant, device, operation, requester, schedule_at=None):
    return db.execute("INSERT INTO jobs(tenant_id,device_id,operation,schedule_at,requested_by,created_at) VALUES(?,?,?,?,?,?)",
                      (tenant, device, operation, schedule_at, requester, now())).lastrowid


def schedule(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Schedule must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat()


def trigger_rules(db, tenant, device, severity):
    rules = db.execute("SELECT * FROM automation_rules WHERE tenant_id=? AND severity=? AND enabled=1", (tenant, severity)).fetchall()
    count = 0
    for rule in rules:
        if rule["group_id"] is not None and not db.execute(
                "SELECT 1 FROM memberships WHERE tenant_id=? AND device_id=? AND group_id=?",
                (tenant, device, rule["group_id"])).fetchone():
            continue
        queue_job(db, tenant, device, rule["operation"], rule["created_by"])
        count += 1
    return count


def bootstrap(name):
    key = secrets.token_urlsafe(32)
    with connect() as db:
        cur = db.execute("INSERT INTO tenants(name) VALUES(?)", (name,))
        db.execute("INSERT INTO principals(tenant_id,name,role,key_hash) VALUES(?,?,?,?)",
                   (cur.lastrowid, "initial-admin", "admin", hashlib.sha256(key.encode()).hexdigest()))
    print("Save this one-time API key securely; it will not be shown again:")
    print(key)


class Handler(BaseHTTPRequestHandler):
    def static(self):
        asset = ASSETS.get(self.path)
        if not asset:
            return False
        data = (WEB / asset[0]).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", asset[1])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)
        return True

    def send(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 4194304:
            raise ValueError("Body too large")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return value

    def principal(self, db):
        header = self.headers.get("Authorization", "")
        key = header[7:] if header.startswith("Bearer ") else ""
        if not key:
            return None
        digest = hashlib.sha256(key.encode()).hexdigest()
        # Compare fixed-size hashes rather than raw tokens.
        for row in db.execute("SELECT * FROM principals WHERE active=1"):
            if hmac.compare_digest(row["key_hash"], digest):
                return row
        return None

    def audit(self, db, user, action, target):
        db.execute("INSERT INTO audit(tenant_id,principal_id,action,target,created_at) VALUES(?,?,?,?,?)",
                   (user["tenant_id"], user["id"], action, target, now()))

    def handle_request(self, method):
        path = self.path.split("?", 1)[0].strip("/").split("/")
        if path == ["health"]:
            return self.send(200, {"status": "ok"})
        with connect() as db:
            user = self.principal(db)
            if not user:
                return self.send(401, {"error": "Authentication required"})
            tenant = user["tenant_id"]
            role = user["role"]
            if path == ["me"] and method == "GET":
                name = db.execute("SELECT name FROM tenants WHERE id=?", (tenant,)).fetchone()[0]
                return self.send(200, {"id": user["id"], "tenant": name, "name": user["name"], "role": role})
            if path == ["preferences"] and method == "GET":
                row = db.execute("SELECT widgets,refresh_seconds FROM preferences WHERE principal_id=? AND tenant_id=?", (user["id"], tenant)).fetchone()
                return self.send(200, {"widgets": json.loads(row["widgets"]), "refresh_seconds": row["refresh_seconds"]} if row else
                                 {"widgets": {"devices": True, "tasks": True, "events": True}, "refresh_seconds": 0})
            if path == ["preferences"] and method == "POST":
                data = self.body()
                widgets = data.get("widgets")
                refresh = data.get("refresh_seconds")
                if not isinstance(widgets, dict) or set(widgets) != {"devices", "tasks", "events"} or any(type(value) is not bool for value in widgets.values()) or refresh not in (0, 30, 60):
                    return self.send(400, {"error": "Invalid dashboard preferences"})
                db.execute("INSERT INTO preferences(principal_id,tenant_id,widgets,refresh_seconds,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(principal_id) DO UPDATE SET widgets=excluded.widgets,refresh_seconds=excluded.refresh_seconds,updated_at=excluded.updated_at",
                           (user["id"], tenant, json.dumps(widgets), refresh, now()))
                return self.send(200, {"widgets": widgets, "refresh_seconds": refresh})
            resource = path[0]
            if method == "POST" and path == ["jobs", "approve-batch"]:
                if role != "admin":
                    return self.send(403, {"error": "Admin required"})
                ids = self.body().get("job_ids")
                if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(value) is not int for value in ids) or len(set(ids)) != len(ids):
                    return self.send(400, {"error": "Choose 1 to 100 distinct job IDs"})
                db.execute("BEGIN IMMEDIATE")
                placeholders = ",".join("?" for _ in ids)
                jobs = db.execute(f"SELECT id,state,requested_by FROM jobs WHERE tenant_id=? AND id IN ({placeholders})", [tenant, *ids]).fetchall()
                if len(jobs) != len(ids) or any(job["state"] != "pending_approval" or job["requested_by"] == user["id"] for job in jobs):
                    return self.send(409, {"error": "Every job must be pending and requested by another admin"})
                db.executemany("UPDATE jobs SET state='approved',approved_by=? WHERE tenant_id=? AND id=? AND state='pending_approval'",
                               [(user["id"], tenant, job_id) for job_id in ids])
                self.audit(db, user, "approve_batch", ",".join(map(str, ids)))
                return self.send(200, {"approved": ids})
            if method == "GET" and resource == "exports" and len(path) == 2:
                columns = {
                    "devices": ("id", "name", "address", "kind", "status", "notes"),
                    "groups": ("id", "name"),
                    "memberships": ("device_id", "group_id"),
                    "events": ("id", "device_id", "severity", "message", "status", "created_at"),
                    "jobs": ("id", "device_id", "operation", "schedule_at", "state", "created_at"),
                }.get(path[1])
                if not columns:
                    return self.send(404, {"error": "Unknown export"})
                fields = ",".join(columns)
                rows = [list(row) for row in db.execute(f"SELECT {fields} FROM {path[1]} WHERE tenant_id=? ORDER BY id DESC" if path[1] != "memberships" else
                                                       f"SELECT {fields} FROM memberships WHERE tenant_id=? ORDER BY device_id,group_id", (tenant,))]
                return self.send(200, {"name": path[1], "columns": columns, "rows": rows})
            if resource not in ("devices", "groups", "memberships", "links", "events", "jobs", "audit", "automation_rules", "deployments", "schedules", "schedule_runs", "users", "config_drafts", "scripts"):
                return self.send(404, {"error": "Not found"})
            if method == "GET" and path == ["users"]:
                if role != "admin":
                    return self.send(403, {"error": "Admin required"})
                return self.send(200, [dict(row) for row in db.execute("SELECT id,name,role,active FROM principals WHERE tenant_id=? ORDER BY id", (tenant,))])
            if method == "POST" and path == ["users"]:
                if role != "admin":
                    return self.send(403, {"error": "Admin required"})
                data = self.body()
                name, new_role = str(data["name"]).strip(), str(data["role"])
                if not name or len(name) > 100 or new_role not in ("admin", "operator", "viewer"):
                    return self.send(400, {"error": "Invalid user name or role"})
                token = secrets.token_urlsafe(32)
                cur = db.execute("INSERT INTO principals(tenant_id,name,role,key_hash) VALUES(?,?,?,?)",
                                 (tenant, name, new_role, hashlib.sha256(token.encode()).hexdigest()))
                self.audit(db, user, "create_user", str(cur.lastrowid))
                return self.send(201, {"id": cur.lastrowid, "api_key": token})
            if method == "POST" and resource == "users" and len(path) == 3 and path[2] == "revoke":
                if role != "admin":
                    return self.send(403, {"error": "Admin required"})
                target = int(path[1])
                if target == user["id"]:
                    return self.send(409, {"error": "Cannot revoke current account"})
                result = db.execute("UPDATE principals SET active=0 WHERE id=? AND tenant_id=?", (target, tenant))
                if result.rowcount == 0:
                    return self.send(404, {"error": "User not found"})
                self.audit(db, user, "revoke_user", str(target))
                return self.send(200, {"active": False})
            if method == "POST" and path == ["devices", "import"]:
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                rows = self.body().get("rows")
                if not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
                    return self.send(400, {"error": "Provide 1 to 5000 device rows"})
                cleaned = []
                for row in rows:
                    if not isinstance(row, dict):
                        return self.send(400, {"error": "Invalid row"})
                    name, address, kind = (str(row.get(field, "")).strip() for field in ("name", "address", "kind"))
                    if not all((name, address, kind)) or max(map(len, (name, address, kind))) > 255:
                        return self.send(400, {"error": "Each row needs name, address and kind, at most 255 characters"})
                    cleaned.append((tenant, name, address, kind, str(row.get("notes", ""))[:1000]))
                if len({item[1] for item in cleaned}) != len(cleaned):
                    return self.send(400, {"error": "Duplicate names in upload"})
                db.executemany("INSERT INTO devices(tenant_id,name,address,kind,notes) VALUES(?,?,?,?,?)", cleaned)
                self.audit(db, user, "import_devices", str(len(cleaned)))
                return self.send(201, {"imported": len(cleaned)})
            if method == "POST" and resource == "devices" and len(path) == 3 and path[2] == "config":
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                device_id = int(path[1])
                if not db.execute("SELECT 1 FROM devices WHERE id=? AND tenant_id=?", (device_id, tenant)).fetchone():
                    return self.send(404, {"error": "Device not found"})
                content = self.body().get("content")
                if not isinstance(content, str) or not content.strip() or len(content.encode()) > 65536:
                    return self.send(400, {"error": "Configuration draft must be 1 to 65536 bytes"})
                cur = db.execute("INSERT INTO config_drafts(tenant_id,device_id,content,created_by,created_at) VALUES(?,?,?,?,?)",
                                 (tenant, device_id, content, user["id"], now()))
                self.audit(db, user, "create_config_draft", str(cur.lastrowid))
                return self.send(201, {"id": cur.lastrowid, "state": "draft_only"})
            if method == "POST" and resource == "devices" and len(path) == 3 and path[2] == "update":
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                data = self.body()
                fields = {field: str(data[field]).strip() for field in ("name", "address", "kind", "notes") if field in data}
                if not fields or any(not value for field, value in fields.items() if field != "notes") or any(len(value) > 1000 for value in fields.values()):
                    return self.send(400, {"error": "Invalid device update"})
                assignments = ",".join(f"{field}=?" for field in fields)
                result = db.execute(f"UPDATE devices SET {assignments} WHERE id=? AND tenant_id=?", [*fields.values(), int(path[1]), tenant])
                if not result.rowcount:
                    return self.send(404, {"error": "Device not found"})
                self.audit(db, user, "update_device", path[1])
                return self.send(200, {"updated": True})
            if method == "GET" and resource == "config_drafts" and len(path) == 1:
                return self.send(200, [dict(row) for row in db.execute("SELECT id,device_id,created_at FROM config_drafts WHERE tenant_id=? ORDER BY id DESC LIMIT 500", (tenant,))])
            if method == "GET" and resource == "config_drafts" and len(path) == 2:
                row = db.execute("SELECT id,device_id,content,created_at FROM config_drafts WHERE tenant_id=? AND id=?", (tenant, int(path[1]))).fetchone()
                return self.send(200, dict(row)) if row else self.send(404, {"error": "Draft not found"})
            if method == "GET" and resource == "scripts" and len(path) == 1:
                return self.send(200, [dict(row) for row in db.execute("SELECT id,name,sha256,status,created_at FROM scripts WHERE tenant_id=? ORDER BY id DESC LIMIT 500", (tenant,))])
            if method == "GET" and resource == "scripts" and len(path) == 2:
                if role != "admin":
                    return self.send(403, {"error": "Admin required"})
                row = db.execute("SELECT id,name,content,sha256,status,created_at FROM scripts WHERE tenant_id=? AND id=?", (tenant, int(path[1]))).fetchone()
                return self.send(200, dict(row)) if row else self.send(404, {"error": "Script not found"})
            if method == "POST" and path == ["scripts"]:
                if role != "admin":
                    return self.send(403, {"error": "Admin required"})
                data = self.body()
                name, content = data.get("name"), data.get("content")
                if not isinstance(name, str) or not name.lower().endswith((".yml", ".yaml", ".sh")) or "/" in name or "\\" in name or len(name) > 120:
                    return self.send(400, {"error": "Use a .yml, .yaml or .sh filename without a path"})
                if not isinstance(content, str) or not content.strip() or len(content.encode()) > 131072:
                    return self.send(400, {"error": "Script must be 1 to 131072 bytes"})
                digest = hashlib.sha256(content.encode()).hexdigest()
                cur = db.execute("INSERT INTO scripts(tenant_id,name,content,sha256,created_by,created_at) VALUES(?,?,?,?,?,?)",
                                 (tenant, name, content, digest, user["id"], now()))
                self.audit(db, user, "store_script", str(cur.lastrowid))
                return self.send(201, {"id": cur.lastrowid, "sha256": digest, "status": "stored_not_executable"})
            if method == "GET" and len(path) == 1 and resource != "deployments":
                order = "device_id DESC,group_id DESC" if resource == "memberships" else "id DESC"
                rows = db.execute(f"SELECT * FROM {resource} WHERE tenant_id=? ORDER BY {order} LIMIT 500", (tenant,))
                result = [dict(row) for row in rows]
                if resource == "jobs":
                    for item in result:
                        item["can_approve"] = role == "admin" and item["state"] == "pending_approval" and item["requested_by"] != user["id"]
                        item.pop("requested_by", None)
                        item.pop("approved_by", None)
                if resource == "automation_rules":
                    for item in result:
                        item.pop("created_by", None)
                if resource == "schedules":
                    for item in result:
                        item.pop("created_by", None)
                return self.send(200, result)
            if method == "POST" and resource == "schedules" and len(path) == 3 and path[2] in ("pause", "resume"):
                if role != "admin":
                    return self.send(403, {"error": "Admin required"})
                wanted = 0 if path[2] == "pause" else 1
                current = db.execute("SELECT id FROM schedules WHERE id=? AND tenant_id=?", (int(path[1]), tenant)).fetchone()
                if not current:
                    return self.send(404, {"error": "Schedule not found"})
                db.execute("UPDATE schedules SET enabled=? WHERE id=? AND tenant_id=?", (wanted, int(path[1]), tenant))
                self.audit(db, user, path[2] + "_schedule", path[1])
                return self.send(200, {"enabled": bool(wanted)})
            if method == "POST" and resource == "automation_rules" and len(path) == 3 and path[2] in ("enable", "disable"):
                if role != "admin":
                    return self.send(403, {"error": "Admin required"})
                wanted = 1 if path[2] == "enable" else 0
                result = db.execute("UPDATE automation_rules SET enabled=? WHERE id=? AND tenant_id=?", (wanted, int(path[1]), tenant))
                if not result.rowcount:
                    return self.send(404, {"error": "Rule not found"})
                self.audit(db, user, path[2] + "_rule", path[1])
                return self.send(200, {"enabled": bool(wanted)})
            if method == "POST" and path == ["deployments"]:
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                data = self.body()
                operation = data.get("operation")
                if operation not in ANSIBLE_OPERATIONS:
                    return self.send(400, {"error": "Unsupported deployment operation"})
                ids = data.get("device_ids", [])
                group_id = data.get("group_id")
                if group_id is not None:
                    if not db.execute("SELECT 1 FROM groups WHERE id=? AND tenant_id=?", (int(group_id), tenant)).fetchone():
                        return self.send(404, {"error": "Group not found"})
                    ids = [row[0] for row in db.execute("SELECT device_id FROM memberships WHERE tenant_id=? AND group_id=?", (tenant, int(group_id)))]
                if not isinstance(ids, list) or not ids or len(ids) > 100 or len(set(map(int, ids))) != len(ids):
                    return self.send(400, {"error": "Select 1 to 100 distinct devices"})
                ids = [int(value) for value in ids]
                found = db.execute(f"SELECT count(*) FROM devices WHERE tenant_id=? AND id IN ({','.join('?' for _ in ids)})", [tenant, *ids]).fetchone()[0]
                if found != len(ids):
                    return self.send(404, {"error": "Device not found in tenant"})
                created = [queue_job(db, tenant, device, operation, user["id"], schedule(data.get("schedule_at"))) for device in ids]
                self.audit(db, user, "create_deployment", f"{operation}:{len(created)}")
                return self.send(201, {"job_ids": created, "state": "pending_approval"})
            if method == "POST" and len(path) == 1:
                if resource not in ("devices", "groups", "links", "events", "jobs", "automation_rules", "schedules"):
                    return self.send(404, {"error": "Not found"})
                if role == "viewer" or resource in ("audit", "schedule_runs") or (resource in ("automation_rules", "schedules") and role != "admin"):
                    return self.send(403, {"error": "Forbidden"})
                data = self.body()
                if resource == "devices":
                    name, address, kind = (str(data[k]).strip() for k in ("name", "address", "kind"))
                    if not all((name, address, kind)):
                        raise ValueError("Device fields cannot be empty")
                    cur = db.execute("INSERT INTO devices(tenant_id,name,address,kind,notes) VALUES(?,?,?,?,?)",
                                     (tenant, name, address, kind, str(data.get("notes", ""))))
                elif resource == "groups":
                    cur = db.execute("INSERT INTO groups(tenant_id,name) VALUES(?,?)", (tenant, str(data["name"])))
                elif resource == "links":
                    source, target = int(data["source_id"]), int(data["target_id"])
                    if source == target or db.execute("SELECT count(*) FROM devices WHERE tenant_id=? AND id IN (?,?)", (tenant, source, target)).fetchone()[0] != 2:
                        return self.send(400, {"error": "Both endpoints must belong to this tenant"})
                    cur = db.execute("INSERT INTO links(tenant_id,source_id,target_id,label) VALUES(?,?,?,?)", (tenant, source, target, str(data.get("label", ""))))
                elif resource == "events":
                    device = int(data["device_id"])
                    if not db.execute("SELECT 1 FROM devices WHERE id=? AND tenant_id=?", (device, tenant)).fetchone():
                        return self.send(404, {"error": "Device not found"})
                    cur = db.execute("INSERT INTO events(tenant_id,device_id,severity,message,created_at) VALUES(?,?,?,?,?)",
                                     (tenant, device, str(data["severity"]), str(data["message"]), now()))
                    trigger_rules(db, tenant, device, str(data["severity"]))
                elif resource == "jobs":
                    device = int(data["device_id"])
                    if not db.execute("SELECT 1 FROM devices WHERE id=? AND tenant_id=?", (device, tenant)).fetchone():
                        return self.send(404, {"error": "Device not found"})
                    operation = str(data["operation"])
                    if operation not in ("backup", "config_review", "patch_review", "remote_session", *ANSIBLE_OPERATIONS):
                        return self.send(400, {"error": "Unsupported operation"})
                    cur = db.execute("INSERT INTO jobs(tenant_id,device_id,operation,schedule_at,requested_by,created_at) VALUES(?,?,?,?,?,?)",
                                     (tenant, device, operation, schedule(data.get("schedule_at")), user["id"], now()))
                elif resource == "automation_rules":
                    group_id = data.get("group_id") or None
                    if group_id is not None and not db.execute("SELECT 1 FROM groups WHERE id=? AND tenant_id=?", (int(group_id), tenant)).fetchone():
                        return self.send(404, {"error": "Group not found"})
                    if data.get("operation") not in ANSIBLE_OPERATIONS:
                        return self.send(400, {"error": "Unsupported operation"})
                    cur = db.execute("INSERT INTO automation_rules(tenant_id,name,severity,operation,group_id,created_by) VALUES(?,?,?,?,?,?)",
                                     (tenant, str(data["name"]), str(data["severity"]), data["operation"], group_id, user["id"]))
                elif resource == "schedules":
                    group_id = int(data["group_id"])
                    if not db.execute("SELECT 1 FROM groups WHERE id=? AND tenant_id=?", (group_id, tenant)).fetchone():
                        return self.send(404, {"error": "Group not found"})
                    if data.get("operation") not in ANSIBLE_OPERATIONS:
                        return self.send(400, {"error": "Unsupported operation"})
                    interval = int(data["interval_seconds"])
                    if not 300 <= interval <= 2592000:
                        return self.send(400, {"error": "Interval must be 5 minutes to 30 days"})
                    start = schedule(data["next_run_at"])
                    if not start:
                        return self.send(400, {"error": "Start time required"})
                    cur = db.execute("INSERT INTO schedules(tenant_id,name,group_id,operation,interval_seconds,next_run_at,created_by) VALUES(?,?,?,?,?,?,?)",
                                     (tenant, str(data["name"]), group_id, data["operation"], interval, start, user["id"]))
                self.audit(db, user, "create_" + resource, str(cur.lastrowid))
                return self.send(201, {"id": cur.lastrowid})
            if method == "POST" and resource == "groups" and len(path) == 3 and path[2] == "rename":
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                name = str(self.body().get("name", "")).strip()
                if not name or len(name) > 100:
                    return self.send(400, {"error": "Invalid group name"})
                result = db.execute("UPDATE groups SET name=? WHERE id=? AND tenant_id=?", (name, int(path[1]), tenant))
                if not result.rowcount:
                    return self.send(404, {"error": "Group not found"})
                self.audit(db, user, "rename_group", path[1])
                return self.send(200, {"name": name})
            if method == "POST" and resource == "devices" and len(path) == 4 and path[2] == "groups":
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                device, group = int(path[1]), int(path[3])
                if not db.execute("SELECT 1 FROM devices WHERE id=? AND tenant_id=?", (device, tenant)).fetchone() or not db.execute("SELECT 1 FROM groups WHERE id=? AND tenant_id=?", (group, tenant)).fetchone():
                    return self.send(404, {"error": "Device or group not found"})
                db.execute("INSERT OR IGNORE INTO memberships VALUES(?,?,?)", (tenant, device, group))
                self.audit(db, user, "assign_group", f"{device}:{group}")
                return self.send(200, {"assigned": True})
            if method == "POST" and resource == "devices" and len(path) == 5 and path[2] == "groups" and path[4] == "remove":
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                device, group = int(path[1]), int(path[3])
                result = db.execute("DELETE FROM memberships WHERE tenant_id=? AND device_id=? AND group_id=?", (tenant, device, group))
                if not result.rowcount:
                    return self.send(404, {"error": "Membership not found"})
                self.audit(db, user, "remove_group", f"{device}:{group}")
                return self.send(200, {"assigned": False})
            if method == "POST" and resource == "jobs" and len(path) == 3 and path[2] == "approve":
                if role != "admin":
                    return self.send(403, {"error": "Admin required"})
                job = db.execute("SELECT * FROM jobs WHERE id=? AND tenant_id=?", (int(path[1]), tenant)).fetchone()
                if not job:
                    return self.send(404, {"error": "Job not found"})
                if job["state"] != "pending_approval" or job["requested_by"] == user["id"]:
                    return self.send(409, {"error": "A different admin must approve a pending job"})
                db.execute("UPDATE jobs SET state='approved', approved_by=? WHERE id=? AND tenant_id=?", (user["id"], job["id"], tenant))
                self.audit(db, user, "approve_job", str(job["id"]))
                return self.send(200, {"state": "approved", "execution": "not_implemented"})
            if method == "POST" and resource == "jobs" and len(path) == 3 and path[2] == "cancel":
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                result = db.execute("UPDATE jobs SET state='cancelled' WHERE id=? AND tenant_id=? AND state IN ('pending_approval','approved')", (int(path[1]), tenant))
                if not result.rowcount:
                    return self.send(409, {"error": "Job is unavailable or already running"})
                self.audit(db, user, "cancel_job", path[1])
                return self.send(200, {"state": "cancelled"})
            if method == "POST" and resource == "events" and len(path) == 3 and path[2] in ("acknowledge", "resolve"):
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                wanted = "acknowledged" if path[2] == "acknowledge" else "resolved"
                result = db.execute("UPDATE events SET status=? WHERE id=? AND tenant_id=? AND status!='resolved'", (wanted, int(path[1]), tenant))
                if not result.rowcount:
                    return self.send(409, {"error": "Event is unavailable or already resolved"})
                self.audit(db, user, wanted + "_event", path[1])
                return self.send(200, {"status": wanted})
            return self.send(404, {"error": "Not found"})

    def dispatch(self, method):
        try:
            self.handle_request(method)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send(400, {"error": str(exc)})
        except sqlite3.IntegrityError:
            self.send(409, {"error": "Conflicting or invalid record"})

    def do_GET(self):
        if self.static():
            return
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "bootstrap":
        bootstrap(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "serve":
        with connect():
            pass
        print(f"Listening at http://{HOST}:{PORT}")
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    else:
        raise SystemExit("Usage: python app.py bootstrap TENANT_NAME | serve")
