import array
import fcntl
import json
import os
import time

VAULT = os.environ.get("VAULT_DIR", "/vault")
RETENTION_S = int(os.environ.get("RETENTION_S", "604800"))
FS_IOC_GETFLAGS, FS_IOC_SETFLAGS, FS_IMMUTABLE_FL = 0x80086601, 0x40086602, 0x10


def set_immutable(path, on):
    fd = os.open(path, os.O_RDONLY)
    try:
        buf = array.array("L", [0])
        fcntl.ioctl(fd, FS_IOC_GETFLAGS, buf, True)
        was = bool(buf[0] & FS_IMMUTABLE_FL)
        buf[0] = (buf[0] | FS_IMMUTABLE_FL) if on else (buf[0] & ~FS_IMMUTABLE_FL)
        fcntl.ioctl(fd, FS_IOC_SETFLAGS, buf)
        return was
    finally:
        os.close(fd)


print(json.dumps({"event": "sealer-start", "vault": VAULT, "retention_s": RETENTION_S}), flush=True)
while True:
    for fname in sorted(os.listdir(VAULT)):
        if fname.startswith("."):
            continue
        path = os.path.join(VAULT, fname)
        age = time.time() - os.path.getmtime(path)
        if age > RETENTION_S:
            set_immutable(path, False)
            os.remove(path)
            print(json.dumps({"event": "expired", "file": fname}), flush=True)
        elif not set_immutable(path, True):
            print(json.dumps({"event": "sealed", "file": fname, "t": round(time.time(), 3)}), flush=True)
    time.sleep(2)
