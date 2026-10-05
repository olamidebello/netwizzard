"""Opt-in Ansible worker for approved, scheduled deployment jobs."""
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import app

PLAYBOOKS = {
    "ansible_check": Path(__file__).with_name("ansible") / "check.yml",
    "ansible_deploy": Path(__file__).with_name("ansible") / "deploy.yml",
}
HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


def validated_address(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if not HOSTNAME.fullmatch(value) or ".." in value:
            raise ValueError("Invalid device address")
    return value


def run_job(job):
    if job["operation"] not in PLAYBOOKS:
        raise ValueError("No allowlisted Ansible playbook")
    address = validated_address(job["address"])
    inventory = {"all": {"hosts": {"target": {"ansible_host": address}}}}
    with tempfile.TemporaryDirectory(prefix="netwizzard-ansible-") as folder:
        inventory_path = Path(folder) / "inventory.json"
        inventory_path.write_text(json.dumps(inventory))
        env = dict(os.environ)
        env["ANSIBLE_HOST_KEY_CHECKING"] = "True"
        env["ANSIBLE_STDOUT_CALLBACK"] = "default"
        env["ANSIBLE_RETRY_FILES_ENABLED"] = "False"
        # Credentials and SSH identity come from the worker's protected environment.
        result = subprocess.run(
            ["ansible-playbook", "-i", str(inventory_path), "--limit", "target", str(PLAYBOOKS[job["operation"]])],
            env=env, cwd=str(Path(__file__).parent), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900, check=False)
        return result.returncode == 0


def claim_one(db):
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("""SELECT jobs.*, devices.address FROM jobs
        JOIN devices ON devices.id=jobs.device_id AND devices.tenant_id=jobs.tenant_id
        WHERE jobs.state='approved' AND jobs.operation IN ('ansible_check','ansible_deploy')
        AND (jobs.schedule_at IS NULL OR jobs.schedule_at <= ?)
        ORDER BY jobs.id LIMIT 1""", (app.now(),)).fetchone()
    if row:
        db.execute("UPDATE jobs SET state='running' WHERE id=? AND state='approved'", (row["id"],))
    db.commit()
    return row


def run_once(executor=run_job):
    if os.environ.get("NETWIZZARD_ANSIBLE_ENABLED") != "1":
        raise RuntimeError("Set NETWIZZARD_ANSIBLE_ENABLED=1 on the protected worker host")
    with app.connect() as db:
        job = claim_one(db)
        if not job:
            return False
        try:
            succeeded = executor(job)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            succeeded = False
        state = "succeeded" if succeeded else "failed"
        db.execute("UPDATE jobs SET state=? WHERE id=? AND state='running'", (state, job["id"]))
        db.execute("INSERT INTO events(tenant_id,device_id,severity,message,created_at) VALUES(?,?,?,?,?)",
                   (job["tenant_id"], job["device_id"], "info" if succeeded else "critical",
                    f"Ansible {job['operation']} job #{job['id']} {state}", app.now()))
        return True


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("once", "loop"):
        raise SystemExit("Usage: python worker.py once|loop")
    while True:
        processed = run_once()
        if sys.argv[1] == "once":
            break
        if not processed:
            time.sleep(5)
