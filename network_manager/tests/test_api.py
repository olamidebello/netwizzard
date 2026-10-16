import importlib.util
import contextlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO, StringIO

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

    def platform_key(self):
        import hashlib
        with app.connect() as db:
            db.execute("INSERT INTO platform_admins(name,key_hash) VALUES('owner',?)", (hashlib.sha256(b'platform').hexdigest(),))
        return "platform"

    def register(self, email="new@example.com"):
        return self.call("POST", "/auth/register", key="", data={"email":email,"name":"New user","tenant_name":"New workspace","password":"long-test-password","role":"super_admin","principal_id":1,"terms_accepted":True,"policy_version":app.POLICY_VERSION})

    def login(self, email="new@example.com", password="long-test-password"):
        return self.call("POST", "/auth/login", key="", data={"email":email,"password":password})

    def test_registration_approval_and_isolated_password_login(self):
        platform = self.platform_key()
        self.assertEqual(self.register()[0], 202)
        self.assertEqual(self.login()[0], 403)
        self.assertEqual(self.call("POST", "/platform/accounts/1/approve", data={})[0], 401)
        self.assertEqual(self.call("POST", "/platform/accounts/1/approve", key=platform, data={})[0], 200)
        status, result = self.login()
        self.assertEqual(status,200)
        token=result["token"]
        me=self.call("GET","/me",key=token)[1]
        self.assertEqual(me["role"],"admin")
        self.assertEqual(me["tenant"],"New workspace")
        self.assertEqual(self.call("GET","/devices",key=token)[1],[])
        self.assertEqual(self.call("GET","/platform/accounts",key=token)[0],401)
        with app.connect() as db:
            account=db.execute("SELECT * FROM accounts").fetchone()
            self.assertNotIn("long-test-password",account["password_hash"])
            self.assertIsNone(account["platform_id"])
            self.assertNotEqual(db.execute("SELECT token_hash FROM account_sessions").fetchone()[0],token)
        self.assertEqual(self.call("POST","/auth/logout",key=token,data={})[0],200)
        self.assertEqual(self.call("GET","/me",key=token)[0],401)

    def test_password_enrollment_platform_login_and_key_rotation(self):
        platform=self.platform_key()
        self.assertEqual(self.call("POST","/auth/enroll",key=platform,data={"email":"owner@example.com","password":"long-test-password"})[0],201)
        token=self.login("owner@example.com")[1]["token"]
        self.assertEqual(self.call("GET","/platform/me",key=token)[0],200)
        self.assertEqual(self.call("GET","/me",key=token)[0],401)
        rotated=self.call("POST","/auth/rotate-key",key=token,data={})[1]["api_key"]
        self.assertEqual(self.call("GET","/platform/me",key=platform)[0],401)
        self.assertEqual(self.call("GET","/platform/me",key=rotated)[0],200)
        self.assertEqual(self.call("GET","/platform/me",key=token)[0],200)

    def test_password_change_and_expiry_revoke_sessions(self):
        self.call("POST","/auth/enroll",data={"email":"admin@example.com","password":"long-test-password"})
        first=self.login("admin@example.com")[1]["token"]
        second=self.login("admin@example.com")[1]["token"]
        self.assertEqual(self.call("POST","/auth/password",key=first,data={"current_password":"wrong","password":"changed-test-password"})[0],403)
        self.assertEqual(self.call("POST","/auth/password",key=first,data={"current_password":"long-test-password","password":"changed-test-password"})[0],200)
        self.assertEqual(self.call("GET","/me",key=second)[0],401)
        self.assertEqual(self.login("admin@example.com")[0],401)
        token=self.login("admin@example.com","changed-test-password")[1]["token"]
        with app.connect() as db: db.execute("UPDATE account_sessions SET expires_at=0")
        self.assertEqual(self.call("GET","/me",key=token)[0],401)

    def test_registration_settings_validation_and_throttling(self):
        platform=self.platform_key()
        self.assertEqual(self.call("POST","/auth/register",key="",data={"email":"invalid"})[0],400)
        self.assertEqual(self.call("POST","/platform/settings",key=platform,data={"registration_open":False})[0],200)
        self.assertEqual(self.register()[0],403)
        self.call("POST","/platform/settings",key=platform,data={"registration_open":True})
        for _ in range(10): self.login("missing@example.com", "wrong")
        self.assertEqual(self.login("missing@example.com","wrong")[0],429)

    def test_suspend_reactivate_reject_and_duplicate_approval(self):
        platform=self.platform_key()
        self.register()
        self.call("POST","/platform/accounts/1/approve",key=platform,data={})
        self.assertEqual(self.call("POST","/platform/accounts/1/approve",key=platform,data={})[0],409)
        token=self.login()[1]["token"]
        self.call("POST","/platform/accounts/1/suspend",key=platform,data={})
        self.assertEqual(self.call("GET","/me",key=token)[0],401)
        self.assertEqual(self.login()[0],403)
        self.call("POST","/platform/accounts/1/activate",key=platform,data={})
        self.assertEqual(self.login()[0],200)
        self.register("reject@example.com")
        self.call("POST","/platform/accounts/2/reject",key=platform,data={})
        self.assertEqual(self.login("reject@example.com")[0],403)

    def test_key_recovery_is_bound_to_original_user(self):
        self.call("POST","/auth/enroll",data={"email":"recover@example.com","password":"long-test-password"})
        token=self.login("recover@example.com")[1]["token"]
        self.assertEqual(self.call("POST","/auth/enroll",key="b",data={"email":"recover@example.com","password":"different-password"})[0],409)
        self.assertEqual(self.call("POST","/auth/enroll",key="a",data={"email":"recover@example.com","password":"different-password"})[0],200)
        self.assertEqual(self.call("GET","/me",key=token)[0],401)
        self.assertEqual(self.login("recover@example.com","different-password")[0],200)

    def test_revoked_tenant_user_cannot_use_password_login(self):
        self.call("POST","/auth/enroll",key="c",data={"email":"second@example.com","password":"long-test-password"})
        token=self.login("second@example.com")[1]["token"]
        self.assertEqual(self.call("GET","/me",key=token)[0],200)
        self.call("POST","/users/2/revoke",data={})
        self.assertEqual(self.call("GET","/me",key=token)[0],401)
        self.assertEqual(self.login("second@example.com")[0],403)

    def zone_config(self):
        from pathlib import Path
        path=Path(self.temp.name)/"zones.json"
        path.write_text(json.dumps([{"id":"office","name":"Office VPN","proxy_host":"169.254.240.6","tenant_ids":[1],"allowed_cidrs":["10.20.0.0/16"],"allowed_ports":[22,443]}]))
        return str(path)

    def test_vpn_zone_scope_session_owner_expiry_and_logout(self):
        from unittest.mock import patch
        with patch.dict(os.environ,{"NETWIZZARD_VPN_ZONES":self.zone_config()}):
            self.assertEqual(len(self.call("GET","/vpn/zones")[1]),1)
            self.assertEqual(self.call("GET","/vpn/zones",key="b")[1],[])
            self.assertEqual(self.call("POST","/vpn/connect",key="b",data={"mode":"platform","zone_id":"office"})[0],403)
            result=self.call("POST","/vpn/connect",data={"mode":"platform","zone_id":"office"})[1]
            sid=result["session_id"]
            self.assertNotIn("proxy_host",result)
            self.assertEqual(self.call("POST","/vpn/heartbeat",key="c",data={"session_id":sid})[0],410)
            self.assertEqual(self.call("POST","/vpn/heartbeat",data={"session_id":sid})[0],200)
            self.call("POST","/auth/logout",data={})
            self.assertEqual(self.call("POST","/vpn/heartbeat",data={"session_id":sid})[0],410)
            sid=self.call("POST","/vpn/connect",data={"mode":"platform","zone_id":"office"})[1]["session_id"]
            with app.connect() as db:db.execute("UPDATE vpn_sessions SET deadline=0")
            self.assertEqual(self.call("POST","/vpn/heartbeat",data={"session_id":sid})[0],410)

    def test_vpn_device_allowlist_and_failure_never_fall_back(self):
        from unittest.mock import patch
        with patch.dict(os.environ,{"NETWIZZARD_VPN_ZONES":self.zone_config()}):
            device=self.call("POST","/devices",data={"name":"vpn-target","address":"10.20.0.4","kind":"router"})[1]["id"]
            foreign=self.call("POST","/devices",key="b",data={"name":"other","address":"10.20.0.5","kind":"router"})[1]["id"]
            sid=self.call("POST","/vpn/connect",data={"mode":"platform","zone_id":"office"})[1]["session_id"]
            def check(device_id=device,port=443):return self.call("POST","/vpn/check",data={"session_id":sid,"device_id":device_id,"port":port})
            with patch.object(app,"vpn_connect_check",return_value=True) as proxy:
                self.assertEqual(check()[0],200)
                proxy.assert_called_once()
                self.assertEqual(check(port=80)[0],400)
                self.assertEqual(check(device_id=foreign)[0],404)
            with patch.object(app,"vpn_connect_check",side_effect=OSError("no VPN")) as proxy:
                self.assertEqual(check()[0],502)
                proxy.assert_called_once()
            with app.connect() as db:db.execute("UPDATE devices SET address='169.254.169.254' WHERE id=?",(device,))
            with patch.object(app,"vpn_connect_check") as proxy:
                self.assertEqual(check()[0],400)
                proxy.assert_not_called()

    def test_local_vpn_mode_has_no_server_network_effect(self):
        from unittest.mock import patch
        with patch.object(app,"vpn_zones",return_value=[]),patch.object(app,"vpn_connect_check") as proxy:
            sid=self.call("POST","/vpn/connect",data={"mode":"local"})[1]["session_id"]
            self.assertEqual(self.call("POST","/vpn/check",data={"session_id":sid,"device_id":1,"port":443})[0],409)
            proxy.assert_not_called()
            self.assertEqual(self.call("POST","/vpn/disconnect",data={"session_id":sid})[0],200)
            self.assertEqual(self.call("POST","/vpn/heartbeat",data={"session_id":sid})[0],410)

    def test_vpn_zone_withdrawal_and_session_limit(self):
        from unittest.mock import patch
        from pathlib import Path
        path=self.zone_config()
        with patch.dict(os.environ,{"NETWIZZARD_VPN_ZONES":path}):
            sid=self.call("POST","/vpn/connect",data={"mode":"platform","zone_id":"office"})[1]["session_id"]
            zones=json.loads(Path(path).read_text());zones[0]["tenant_ids"]=[];Path(path).write_text(json.dumps(zones))
            self.assertEqual(self.call("POST","/vpn/heartbeat",data={"session_id":sid})[0],410)
            for _ in range(5):self.assertEqual(self.call("POST","/vpn/connect",data={"mode":"local"})[0],201)
            self.assertEqual(self.call("POST","/vpn/connect",data={"mode":"local"})[0],409)

    def test_vpn_connect_proxy_protocol(self):
        from unittest.mock import patch,MagicMock
        connection=MagicMock()
        connection.recv.return_value=b"HTTP/1.1 200 Connection established\r\n\r\n"
        with patch.object(app.socket,"create_connection") as factory:
            factory.return_value.__enter__.return_value=connection
            self.assertTrue(app.vpn_connect_check({"proxy_host":"169.254.240.6"},"10.20.0.4",443))
            connection.sendall.assert_called_once_with(b"CONNECT 10.20.0.4:443 HTTP/1.1\r\nHost: 10.20.0.4:443\r\nConnection: close\r\n\r\n")
            connection.recv.return_value=b"HTTP/1.1 403 Forbidden\r\n"
            self.assertFalse(app.vpn_connect_check({"proxy_host":"169.254.240.6"},"10.20.0.4",443))

    def test_registration_records_policy_acceptance_and_rejects_missing_agreement(self):
        fields={"email":"terms@example.com","name":"Terms user","tenant_name":"Terms workspace","password":"long-test-password"}
        self.assertEqual(self.call("POST","/auth/register",key="",data=fields)[0],400)
        fields.update(terms_accepted=True,policy_version="old")
        self.assertEqual(self.call("POST","/auth/register",key="",data=fields)[0],400)
        fields["policy_version"]=app.POLICY_VERSION
        self.assertEqual(self.call("POST","/auth/register",key="",data=fields)[0],202)
        with app.connect() as db:
            rows=db.execute("SELECT * FROM policy_acceptances").fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["version"],app.POLICY_VERSION)
        self.call("POST","/auth/register",key="",data=fields)
        with app.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM policy_acceptances").fetchone()[0],1)

    def test_legal_request_inbox_is_platform_only_and_resolution_is_audited(self):
        key=self.platform_key()
        request=self.call("POST","/legal/requests",key="",data={"email":"request@example.com","category":"privacy","message":"Please close my account."})
        self.assertEqual(request[0],201)
        rid=request[1]["request_id"]
        self.assertEqual(self.call("GET","/platform/legal-requests")[0],401)
        inbox=self.call("GET","/platform/legal-requests",key=key)[1]
        self.assertEqual(inbox[0]["message"],"Please close my account.")
        self.assertEqual(self.call("POST",f"/platform/legal-requests/{rid}/resolve",key=key,data={})[0],200)
        self.assertIsNotNone(self.call("GET","/platform/legal-requests",key=key)[1][0]["resolved_at"])
        self.assertEqual(self.call("POST","/legal/requests",key="",data={"email":"x","category":"privacy","message":"short"})[0],400)

    def test_root_cli_promotion_retains_password_revokes_sessions_and_separates_tenant(self):
        from unittest.mock import patch
        self.call("POST","/auth/enroll",data={"email":"dobcalldemo@gmail.com","password":"long-test-password"})
        token=self.login("dobcalldemo@gmail.com")[1]["token"]
        with patch.object(app.os,"geteuid",return_value=1000):
            with self.assertRaises(PermissionError):app.promote_platform_account("dobcalldemo@gmail.com")
        with patch.object(app.os,"geteuid",return_value=0), contextlib.redirect_stdout(StringIO()):
            app.promote_platform_account("dobcalldemo@gmail.com")
            app.promote_platform_account("dobcalldemo@gmail.com")
            with self.assertRaises(ValueError):app.promote_platform_account("missing@example.com")
        self.assertEqual(self.call("GET","/me",key=token)[0],401)
        elevated=self.login("dobcalldemo@gmail.com")[1]["token"]
        self.assertEqual(self.call("GET","/platform/me",key=elevated)[1]["role"],"super_admin")
        self.assertEqual(self.call("GET","/me",key=elevated)[0],401)
        with app.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM platform_admins").fetchone()[0],1)

    def test_legal_pages_are_public_static_assets(self):
        for route in ("/signup","/signup.js","/terms","/privacy","/copyright","/contact","/legal.js"):
            self.assertIn(route,app.ASSETS)
            self.assertTrue((app.WEB/app.ASSETS[route][0]).is_file())

    def test_tenant_scope_for_devices_and_links(self):
        _, a = self.call("POST", "/devices", data={"name": "a", "address": "192.0.2.1", "kind": "router"})
        _, b = self.call("POST", "/devices", key="b", data={"name": "b", "address": "192.0.2.2", "kind": "router"})
        self.assertEqual([d["name"] for d in self.call("GET", "/devices")[1]], ["a"])
        self.assertEqual(self.call("POST", "/links", data={"source_id": a["id"], "target_id": b["id"]})[0], 400)
        self.assertEqual(self.call("POST", "/jobs", data={"device_id": b["id"], "operation": "backup"})[0], 404)

    def test_agent_reports_and_vault_references_are_tenant_scoped(self):
        status, created = self.call("POST", "/agents", data={"name": "site-a"})
        self.assertEqual(status, 201)
        report = {"name": "edge", "address": "192.0.2.4", "kind": "router", "status": "down", "cpu_percent": 25}
        self.assertEqual(self.call("POST", "/agent/report", key=created["agent_key"], data=report)[0], 200)
        self.assertEqual(len(self.call("GET", "/events")[1]), 1)
        self.call("POST", "/agent/report", key=created["agent_key"], data=report)
        self.assertEqual(len(self.call("GET", "/events")[1]), 1)
        self.assertEqual(len(self.call("GET", "/telemetry")[1]), 2)
        self.assertEqual(self.call("GET", "/devices", key="b")[1], [])
        device = self.call("GET", "/devices")[1][0]
        self.assertEqual(self.call("POST", f"/devices/{device['id']}/update", data={"credential_ref": "plain-secret"})[0], 400)
        self.assertEqual(self.call("POST", f"/devices/{device['id']}/update", data={"owner_id": 1, "credential_ref": "vault://tenant/edge"})[0], 200)
        self.assertEqual(self.call("POST", f"/devices/{device['id']}/update", key="b", data={"owner_id": 1})[0], 400)
        self.call("POST", f"/agents/{created['id']}/revoke", data={})
        self.assertEqual(self.call("POST", "/agent/report", key=created["agent_key"], data=report)[0], 401)

    def test_platform_admin_provisions_isolated_tenant(self):
        import hashlib
        with app.connect() as db:
            db.execute("INSERT INTO platform_admins(name,key_hash) VALUES(?,?)", ("root", hashlib.sha256(b"platform").hexdigest()))
        self.assertEqual(self.call("GET", "/platform/tenants", key="a")[0], 401)
        status, result = self.call("POST", "/platform/tenants", key="platform", data={"name":"C", "admin_name":"owner"})
        self.assertEqual(status, 201)
        self.assertEqual(self.call("GET", "/me", key=result["api_key"])[1]["tenant"], "C")
        self.assertEqual(self.call("GET", "/platform/tenants", key="platform")[0], 200)
        self.assertEqual(self.call("GET", "/devices", key=result["api_key"])[1], [])
        self.assertEqual(self.call("GET", "/platform/tenants", key=result["api_key"])[0], 401)
        tid = result["tenant_id"]
        self.assertEqual(self.call("POST", f"/platform/tenants/{tid}/admins/{result['admin_id']}/revoke", key="platform", data={})[0], 409)
        second = self.call("POST", f"/platform/tenants/{tid}/admins", key="platform", data={"name":"backup"})[1]
        self.assertEqual(self.call("POST", f"/platform/tenants/{tid}/admins/{result['admin_id']}/revoke", key="platform", data={})[0], 200)
        self.assertEqual(self.call("GET", "/me", key=result["api_key"])[0], 401)
        self.assertEqual(self.call("GET", "/me", key=second["api_key"])[0], 200)
        self.assertEqual(len(self.call("GET", "/platform/audit", key="platform")[1]), 3)

    def test_telemetry_export_and_metadata_admin_boundary(self):
        _, agent = self.call("POST", "/agents", data={"name":"monitor"})
        self.call("POST", "/agent/report", key=agent["agent_key"], data={"name":"switch", "address":"192.0.2.5", "kind":"switch", "status":"up"})
        status, export = self.call("GET", "/exports/telemetry")
        self.assertEqual(status, 200)
        self.assertEqual(export["rows"][0][3], "up")
        self.assertEqual(self.call("GET", "/exports/telemetry", key="b")[1]["rows"], [])
        _, operator = self.call("POST", "/users", data={"name":"operator", "role":"operator"})
        device = self.call("GET", "/devices")[1][0]
        self.assertEqual(self.call("POST", f"/devices/{device['id']}/update", key=operator["api_key"], data={"credential_ref":"vault://secret"})[0], 403)

    def test_threshold_alert_dedup_acknowledge_recovery_and_tenant_scope(self):
        _, agent = self.call("POST", "/agents", data={"name":"collector"})
        self.assertEqual(self.call("POST", "/alert_policies", key="b", data={"name":"bad","metric":"cpu_percent","threshold":80,"severity":"critical","group_id":999})[0], 400)
        status, policy = self.call("POST", "/alert_policies", data={"name":"High CPU","metric":"cpu_percent","threshold":80,"severity":"critical"})
        self.assertEqual(status, 201)
        report = {"name":"edge","address":"192.0.2.10","kind":"router","status":"up","cpu_percent":90}
        for _ in range(2):
            self.assertEqual(self.call("POST", "/agent/report", key=agent["agent_key"], data=report)[0], 200)
        alert = self.call("GET", "/alerts")[1][0]
        self.assertEqual(alert["occurrences"], 2)
        self.assertEqual(len(self.call("GET", "/events")[1]), 1)
        self.assertEqual(self.call("GET", "/alerts", key="b")[1], [])
        self.assertEqual(self.call("POST", f"/alerts/{alert['id']}/acknowledge", key="b", data={})[0], 409)
        self.assertEqual(self.call("POST", f"/alerts/{alert['id']}/acknowledge", data={})[0], 200)
        report["cpu_percent"] = 20
        self.call("POST", "/agent/report", key=agent["agent_key"], data=report)
        self.assertEqual(self.call("GET", "/alerts")[1][0]["state"], "resolved")
        self.assertEqual(self.call("GET", "/events")[1][0]["status"], "resolved")
        self.assertEqual(self.call("GET", "/exports/alerts")[1]["rows"][0][3], "resolved")
        self.call("POST", f"/alert_policies/{policy['id']}/pause", data={})
        report["cpu_percent"] = 95
        self.call("POST", "/agent/report", key=agent["agent_key"], data=report)
        self.assertEqual(len(self.call("GET", "/alerts")[1]), 1)

    def test_down_alert_policy_uses_single_event_and_group_boundary(self):
        _, agent = self.call("POST", "/agents", data={"name":"collector"})
        _, group = self.call("POST", "/groups", data={"name":"core"})
        self.call("POST", "/alert_policies", data={"name":"Core down","metric":"status_down","severity":"critical","group_id":group["id"]})
        report = {"name":"router","address":"192.0.2.11","kind":"router","status":"up"}
        _, result = self.call("POST", "/agent/report", key=agent["agent_key"], data=report)
        self.call("POST", f"/devices/{result['device_id']}/groups/{group['id']}", data={})
        report["status"] = "down"
        self.call("POST", "/agent/report", key=agent["agent_key"], data=report)
        self.assertEqual(len(self.call("GET", "/events")[1]), 1)
        self.assertEqual(len(self.call("GET", "/alerts")[1]), 1)
        report["status"] = "up"
        self.call("POST", "/agent/report", key=agent["agent_key"], data=report)
        self.assertEqual(self.call("GET", "/alerts")[1][0]["state"], "resolved")

    def test_expired_worker_claim_requires_review(self):
        import importlib
        import sys
        sys.path.insert(0, ROOT)
        try:
            worker = importlib.import_module("worker")
            worker.app = app
            _, device = self.call("POST", "/devices", data={"name":"host", "address":"192.0.2.1", "kind":"server"})
            _, job = self.call("POST", "/jobs", data={"device_id":device["id"], "operation":"ansible_check"})
            with app.connect() as db:
                db.execute("UPDATE jobs SET state='running',lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (job["id"],))
                db.commit()
                self.assertIsNone(worker.claim_one(db))
                state = db.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
                self.assertEqual(state, "needs_review")
        finally:
            sys.path.remove(ROOT)

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

    def test_batch_approval_is_atomic_and_separate_admin(self):
        _, device = self.call("POST", "/devices", data={"name": "edge", "address": "192.0.2.1", "kind": "router"})
        jobs = [self.call("POST", "/jobs", data={"device_id": device["id"], "operation": "ansible_check"})[1]["id"] for _ in range(2)]
        self.assertEqual(self.call("POST", "/jobs/approve-batch", key="a", data={"job_ids": jobs})[0], 409)
        self.assertTrue(all(item["state"] == "pending_approval" for item in self.call("GET", "/jobs")[1]))
        self.assertEqual(self.call("POST", "/jobs/approve-batch", key="c", data={"job_ids": jobs})[1]["approved"], jobs)
        self.assertTrue(all(item["state"] == "approved" for item in self.call("GET", "/jobs")[1]))
        self.assertEqual(self.call("POST", "/jobs/approve-batch", key="b", data={"job_ids": jobs})[0], 409)

    def test_preferences_and_rule_toggle_are_tenant_scoped(self):
        initial = self.call("GET", "/preferences")[1]
        self.assertEqual(initial["refresh_seconds"], 0)
        prefs = {"widgets": {"devices": True, "tasks": False, "events": True}, "refresh_seconds": 30}
        self.assertEqual(self.call("POST", "/preferences", data=prefs)[1], prefs)
        self.assertEqual(self.call("GET", "/preferences", key="c")[1]["refresh_seconds"], 0)
        self.assertEqual(self.call("POST", "/preferences", data={"widgets": {"devices": True}, "refresh_seconds": 60})[0], 400)
        _, device = self.call("POST", "/devices", data={"name": "edge", "address": "192.0.2.1", "kind": "router"})
        _, rule = self.call("POST", "/automation_rules", data={"name": "critical", "severity": "critical", "operation": "ansible_check"})
        self.assertEqual(self.call("POST", f"/automation_rules/{rule['id']}/disable", key="b", data={})[0], 404)
        self.assertEqual(self.call("POST", f"/automation_rules/{rule['id']}/disable", data={})[1], {"enabled": False})
        self.call("POST", "/events", data={"device_id": device["id"], "severity": "critical", "message": "down"})
        self.assertEqual(self.call("GET", "/jobs")[1], [])
        self.assertEqual(self.call("POST", f"/automation_rules/{rule['id']}/enable", data={})[1], {"enabled": True})
        self.call("POST", "/events", data={"device_id": device["id"], "severity": "critical", "message": "down again"})
        self.assertEqual(len(self.call("GET", "/jobs")[1]), 1)


if __name__ == "__main__":
    unittest.main()
