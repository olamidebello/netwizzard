import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO

ROOT = os.path.dirname(os.path.dirname(__file__))
spec = importlib.util.spec_from_file_location("network_manager", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.DB = os.path.join(self.temp.name, "test.db")
        import hashlib
        with app.connect() as db:
            for tenant, key in (("A", "a"), ("B", "b")):
                tid = db.execute("INSERT INTO tenants(name) VALUES(?)", (tenant,)).lastrowid
                db.execute("INSERT INTO principals(tenant_id,name,role,key_hash) VALUES(?,?,?,?)",
                           (tid, "admin", "admin", hashlib.sha256(key.encode()).hexdigest()))
                if tenant == "A":
                    db.execute("INSERT INTO principals(tenant_id,name,role,key_hash) VALUES(?,?,?,?)",
                               (tid, "second-admin", "admin", hashlib.sha256(b"c").hexdigest()))

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, key="a", data=None):
        handler = object.__new__(app.Handler)
        encoded = json.dumps(data).encode() if data is not None else b""
        handler.path = path
        handler.headers = {"Authorization": "Bearer " + key, "Content-Length": str(len(encoded))}
        handler.rfile = BytesIO(encoded)
        handler.send = lambda status, body: setattr(handler, "result", (status, body))
        handler.dispatch(method)
        return handler.result

    def test_tenant_scope_for_devices_and_links(self):
        _, a = self.call("POST", "/devices", data={"name": "a", "address": "192.0.2.1", "kind": "router"})
        _, b = self.call("POST", "/devices", key="b", data={"name": "b", "address": "192.0.2.2", "kind": "router"})
        self.assertEqual([d["name"] for d in self.call("GET", "/devices")[1]], ["a"])
        self.assertEqual(self.call("POST", "/links", data={"source_id": a["id"], "target_id": b["id"]})[0], 400)
        self.assertEqual(self.call("POST", "/jobs", data={"device_id": b["id"], "operation": "backup"})[0], 404)

    def test_jobs_do_not_execute(self):
        _, d = self.call("POST", "/devices", data={"name": "a", "address": "192.0.2.1", "kind": "router"})
        _, job = self.call("POST", "/jobs", data={"device_id": d["id"], "operation": "backup"})
        self.assertEqual(self.call("POST", f"/jobs/{job['id']}/approve")[0], 409)
        self.assertEqual(self.call("GET", "/jobs")[1][0]["state"], "pending_approval")

    def test_console_identity_is_tenant_scoped(self):
        self.assertEqual(self.call("GET", "/me", key="a"),
                         (200, {"id": 1, "tenant": "A", "name": "admin", "role": "admin"}))
        self.assertEqual(self.call("GET", "/me", key="b")[1]["tenant"], "B")
        self.assertEqual(self.call("GET", "/me", key="invalid")[0], 401)

    def test_static_console_has_security_headers(self):
        handler = object.__new__(app.Handler)
        handler.path = "/"
        handler.wfile = BytesIO()
        headers = {}
        handler.send_response = lambda status: headers.update(status=status)
        handler.send_header = lambda name, value: headers.update({name: value})
        handler.end_headers = lambda: None
        handler.do_GET()
        self.assertEqual(headers["status"], 200)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn(b"Netwizzard Console", handler.wfile.getvalue())

    def test_group_deployment_approval_and_worker(self):
        _, group = self.call("POST", "/groups", data={"name": "edge"})
        _, device = self.call("POST", "/devices", data={"name": "edge-1", "address": "192.0.2.1", "kind": "server"})
        self.call("POST", f"/devices/{device['id']}/groups/{group['id']}", data={})
        status, result = self.call("POST", "/deployments", data={"group_id": group["id"], "operation": "ansible_check"})
        self.assertEqual(status, 201)
        job_id = result["job_ids"][0]
        self.assertEqual(self.call("POST", f"/jobs/{job_id}/approve", key="c", data={})[0], 200)
        self.assertEqual(self.call("GET", "/jobs")[1][0]["state"], "approved")
        import importlib
        import unittest.mock
        import sys
        sys.path.insert(0, ROOT)
        try:
            worker = importlib.import_module("worker")
            worker.app = app
            with unittest.mock.patch.dict(os.environ, {"NETWIZZARD_ANSIBLE_ENABLED": "1"}):
                self.assertTrue(worker.run_once(lambda job: job["address"] == "192.0.2.1"))
        finally:
            sys.path.remove(ROOT)
        self.assertEqual(self.call("GET", "/jobs")[1][0]["state"], "succeeded")

    def test_event_rule_queues_only_approved_target_group(self):
        _, group = self.call("POST", "/groups", data={"name": "edge"})
        _, a = self.call("POST", "/devices", data={"name": "a", "address": "192.0.2.1", "kind": "server"})
        _, b = self.call("POST", "/devices", data={"name": "b", "address": "192.0.2.2", "kind": "server"})
        self.call("POST", f"/devices/{a['id']}/groups/{group['id']}", data={})
        self.call("POST", "/automation_rules", data={"name": "critical edge check", "severity": "critical", "group_id": group["id"], "operation": "ansible_check"})
        for device in (a, b):
            self.call("POST", "/events", data={"device_id": device["id"], "severity": "critical", "message": "down"})
        jobs = self.call("GET", "/jobs")[1]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["device_id"], a["id"])
        self.assertEqual(jobs[0]["state"], "pending_approval")

    def test_recurring_schedule_enqueues_once_and_can_pause(self):
        _, group = self.call("POST", "/groups", data={"name": "edge"})
        _, device = self.call("POST", "/devices", data={"name": "edge-1", "address": "192.0.2.1", "kind": "server"})
        self.call("POST", f"/devices/{device['id']}/groups/{group['id']}", data={})
        status, created = self.call("POST", "/schedules", data={"name": "hourly-check", "group_id": group["id"],
            "operation": "ansible_check", "interval_seconds": 3600, "next_run_at": "2026-10-04T10:00:00Z"})
        self.assertEqual(status, 201)
        self.assertEqual(self.call("GET", "/schedules", key="b")[1], [])
        import importlib
        import sys
        sys.path.insert(0, ROOT)
        try:
            worker = importlib.import_module("worker")
            worker.app = app
            instant = datetime(2026, 10, 4, 11, tzinfo=timezone.utc)
            with app.connect() as db:
                self.assertEqual(worker.enqueue_due(db, instant), 1)
                self.assertEqual(worker.enqueue_due(db, instant), 0)
        finally:
            sys.path.remove(ROOT)
        self.assertEqual(len(self.call("GET", "/jobs")[1]), 1)
        self.assertEqual(self.call("GET", "/schedule_runs")[1][0]["job_count"], 1)
        self.assertEqual(self.call("POST", f"/schedules/{created['id']}/pause", key="b", data={})[0], 404)
        self.assertEqual(self.call("POST", f"/schedules/{created['id']}/pause", data={})[1], {"enabled": False})
        self.assertFalse(self.call("GET", "/schedules")[1][0]["enabled"])

    def test_device_upload_configuration_and_group_management(self):
        rows = [{"name": "a", "address": "192.0.2.1", "kind": "router"},
                {"name": "b", "address": "192.0.2.2", "kind": "switch"}]
        self.assertEqual(self.call("POST", "/devices/import", data={"rows": rows})[1]["imported"], 2)
        self.assertEqual(self.call("POST", "/devices/import", key="b", data={"rows": rows})[0], 201)
        devices = self.call("GET", "/devices")[1]
        _, group = self.call("POST", "/groups", data={"name": "edge"})
        self.assertEqual(self.call("POST", f"/groups/{group['id']}/rename", data={"name": "core"})[1]["name"], "core")
        device = devices[0]["id"]
        self.call("POST", f"/devices/{device}/groups/{group['id']}", data={})
        self.assertEqual(len(self.call("GET", "/memberships")[1]), 1)
        self.assertEqual(self.call("POST", f"/devices/{device}/groups/{group['id']}/remove", data={})[1], {"assigned": False})
        status, draft = self.call("POST", f"/devices/{device}/config", data={"content": "hostname b"})
        self.assertEqual(status, 201)
        self.assertEqual(self.call("GET", f"/config_drafts/{draft['id']}")[1]["content"], "hostname b")
        self.assertEqual(self.call("GET", "/config_drafts", key="b")[1], [])

    def test_user_control_and_script_storage(self):
        status, created = self.call("POST", "/users", data={"name": "tech", "role": "operator"})
        self.assertEqual(status, 201)
        self.assertEqual(self.call("GET", "/me", key=created["api_key"])[1]["role"], "operator")
        self.assertEqual(self.call("GET", "/users", key=created["api_key"])[0], 403)
        status, script = self.call("POST", "/scripts", data={"name": "baseline.yml", "content": "---\n- hosts: all\n"})
        self.assertEqual(status, 201)
        self.assertEqual(script["status"], "stored_not_executable")
        self.assertEqual(self.call("GET", "/scripts", key="b")[1], [])
        self.assertEqual(self.call("POST", "/scripts", key=created["api_key"], data={"name": "bad.sh", "content": "echo hi"})[0], 403)
        self.assertEqual(self.call("POST", f"/users/{created['id']}/revoke", data={})[1], {"active": False})
        self.assertEqual(self.call("GET", "/me", key=created["api_key"])[0], 401)

    def test_export_and_management_actions_stay_in_tenant(self):
        _, device = self.call("POST", "/devices", data={"name": "edge", "address": "192.0.2.1", "kind": "router"})
        self.assertEqual(self.call("POST", f"/devices/{device['id']}/update", data={"name": "edge-new"})[1], {"updated": True})
        self.assertEqual(self.call("POST", f"/devices/{device['id']}/update", key="b", data={"name": "other"})[0], 404)
        exported = self.call("GET", "/exports/devices")[1]
        self.assertEqual(exported["rows"][0][1], "edge-new")
        self.assertEqual(self.call("GET", "/exports/devices", key="b")[1]["rows"], [])
        _, event = self.call("POST", "/events", data={"device_id": device["id"], "severity": "warning", "message": "slow"})
        self.assertEqual(self.call("POST", f"/events/{event['id']}/acknowledge", data={})[1]["status"], "acknowledged")
        self.assertEqual(self.call("POST", f"/events/{event['id']}/resolve", data={})[1]["status"], "resolved")
        self.assertEqual(self.call("POST", f"/events/{event['id']}/resolve", key="b", data={})[0], 409)
        _, job = self.call("POST", "/jobs", data={"device_id": device["id"], "operation": "config_review"})
        self.assertEqual(self.call("POST", f"/jobs/{job['id']}/cancel", data={})[1]["state"], "cancelled")
        self.assertEqual(self.call("POST", f"/jobs/{job['id']}/approve", key="c", data={})[0], 409)


if __name__ == "__main__":
    unittest.main()
