import glob
import hashlib
import json
import os
import time
from http.client import HTTPConnection

VAULT = os.environ.get("VAULT_DIR", "/vault")
TARGET = os.environ.get("DB_TARGET", "db-0.db-headless.aitu-sim.svc.cluster.local")

BACKUP_FILE = os.environ.get("BACKUP_FILE")
manifests = [os.path.join(VAULT, BACKUP_FILE)] if BACKUP_FILE else sorted(glob.glob(os.path.join(VAULT, "db-*.manifest.json")))
if not manifests:
    raise SystemExit("no backup manifest in the vault")
manifest = json.load(open(manifests[-1]))
data = open(os.path.join(VAULT, manifest["file"]), "rb").read()
if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
    raise SystemExit("backup failed its integrity check: " + manifest["file"])
records = json.loads(data)
conn = HTTPConnection(TARGET, 8080, timeout=10)
start = time.time()
for rec in records:
    conn.request("POST", "/db/write", json.dumps(rec), {"Content-Type": "application/json"})
    conn.getresponse().read()
print(json.dumps({"restored_from": manifest["file"], "records": len(records), "verified_sha256": True,
                  "backup_age_s": round(start - manifest["created_epoch"], 1),
                  "restore_s": round(time.time() - start, 1)}), flush=True)
