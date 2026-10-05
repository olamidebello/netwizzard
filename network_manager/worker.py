"""Opt-in Ansible worker for approved, scheduled deployment jobs."""
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
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


def enqueue_due(db, current=None):
    """Atomically enqueue one batch per due schedule; missed intervals do not backfill."""
    current = current or datetime.now(timezone.utc)
    stamp = current.isoformat()
    db.execute("BEGIN IMMEDIATE")
    due = db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at,id LIMIT 25", (stamp,)).fetchall()
    queued = 0
    for item in due:
        ids = [row[0] for row in db.execute("SELECT device_id FROM memberships WHERE tenant_id=? AND group_id=? ORDER BY device_id LIMIT 101",
                                           (item["tenant_id"], item["group_id"]))]
        if len(ids) > 100:
            db.execute("UPDATE schedules SET enabled=0 WHERE id=?", (item["id"],))
            continue
        for device in ids:
            app.queue_job(db, item["tenant_id"], device, item["operation"], item["created_by"])
        db.execute("INSERT INTO schedule_runs(tenant_id,schedule_id,due_at,created_at,job_count) VALUES(?,?,?,?,?)",
                   (item["tenant_id"], item["id"], item["next_run_at"], stamp, len(ids)))
        next_at = current + timedelta(seconds=item["interval_seconds"])
        db.execute("UPDATE schedules SET next_run_at=?,last_run_at=? WHERE id=?",
                   (next_at.isoformat(), stamp, item["id"]))
        queued += len(ids)
    db.commit()
    return queued


def run_once(executor=run_job):
    if os.environ.get("NETWIZZARD_ANSIBLE_ENABLED") != "1":
        raise RuntimeError("Set NETWIZZARD_ANSIBLE_ENABLED=1 on the protected worker host")
    with app.connect() as db:
        enqueue_due(db)
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
