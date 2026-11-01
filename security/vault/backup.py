import hashlib
import json
import os
import time
import urllib.request

NODES = os.environ.get("DB_SOURCES", "db-0.db-headless.aitu-sim.svc.cluster.local,db-1.db-headless.aitu-sim.svc.cluster.local").split(",")
VAULT = os.environ.get("VAULT_DIR", "/vault")

for node in NODES:
    try:
        raw = urllib.request.urlopen(f"http://{node}:8080/db/dump", timeout=10).read()
        source = node
        break
    except OSError:
        continue
else:
    raise SystemExit("no database node reachable")

records = json.loads(raw)["records"]
body = json.dumps(sorted(records, key=lambda r: r["id"]), sort_keys=True).encode()
ids = "\n".join(sorted(r["id"] for r in records)).encode()
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
name = f"db-{stamp}.json"
manifest = {"file": name, "created_epoch": round(time.time(), 3), "records": len(records),
            "sha256": hashlib.sha256(body).hexdigest(), "ids_sha256": hashlib.sha256(ids).hexdigest(), "source": source}

for fname, payload in ((name, body), (name.replace(".json", ".manifest.json"), json.dumps(manifest).encode())):
    tmp = os.path.join(VAULT, "." + fname + ".tmp")
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, os.path.join(VAULT, fname))
print(json.dumps(manifest), flush=True)
