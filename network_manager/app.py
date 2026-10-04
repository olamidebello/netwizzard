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
"""


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    return db


def now():
    return datetime.now(timezone.utc).isoformat()


def bootstrap(name):
    key = secrets.token_urlsafe(32)
    with connect() as db:
        cur = db.execute("INSERT INTO tenants(name) VALUES(?)", (name,))
        db.execute("INSERT INTO principals(tenant_id,name,role,key_hash) VALUES(?,?,?,?)",
                   (cur.lastrowid, "initial-admin", "admin", hashlib.sha256(key.encode()).hexdigest()))
    print("Save this one-time API key securely; it will not be shown again:")
    print(key)


class Handler(BaseHTTPRequestHandler):
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
        if length > 1048576:
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
        for row in db.execute("SELECT * FROM principals"):
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
            resource = path[0]
            if resource not in ("devices", "groups", "links", "events", "jobs", "audit"):
                return self.send(404, {"error": "Not found"})
            if method == "GET" and len(path) == 1:
                rows = db.execute(f"SELECT * FROM {resource} WHERE tenant_id=? ORDER BY id DESC LIMIT 500", (tenant,))
                result = [dict(row) for row in rows]
                if resource == "jobs":
                    for item in result:
                        item.pop("requested_by", None)
                        item.pop("approved_by", None)
                return self.send(200, result)
            if method == "POST" and len(path) == 1:
                if role == "viewer" or resource == "audit":
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
                elif resource == "jobs":
                    device = int(data["device_id"])
                    if not db.execute("SELECT 1 FROM devices WHERE id=? AND tenant_id=?", (device, tenant)).fetchone():
                        return self.send(404, {"error": "Device not found"})
                    operation = str(data["operation"])
                    if operation not in ("backup", "config_review", "patch_review", "remote_session"):
                        return self.send(400, {"error": "Unsupported operation"})
                    cur = db.execute("INSERT INTO jobs(tenant_id,device_id,operation,schedule_at,requested_by,created_at) VALUES(?,?,?,?,?,?)",
                                     (tenant, device, operation, data.get("schedule_at"), user["id"], now()))
                self.audit(db, user, "create_" + resource, str(cur.lastrowid))
                return self.send(201, {"id": cur.lastrowid})
            if method == "POST" and resource == "devices" and len(path) == 4 and path[2] == "groups":
                if role == "viewer":
                    return self.send(403, {"error": "Forbidden"})
                device, group = int(path[1]), int(path[3])
                if not db.execute("SELECT 1 FROM devices WHERE id=? AND tenant_id=?", (device, tenant)).fetchone() or not db.execute("SELECT 1 FROM groups WHERE id=? AND tenant_id=?", (group, tenant)).fetchone():
                    return self.send(404, {"error": "Device or group not found"})
                db.execute("INSERT OR IGNORE INTO memberships VALUES(?,?,?)", (tenant, device, group))
                self.audit(db, user, "assign_group", f"{device}:{group}")
                return self.send(200, {"assigned": True})
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
            return self.send(404, {"error": "Not found"})

    def dispatch(self, method):
        try:
            self.handle_request(method)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send(400, {"error": str(exc)})
        except sqlite3.IntegrityError:
            self.send(409, {"error": "Conflicting or invalid record"})

    def do_GET(self):
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
