import importlib.util
import json
import os
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
