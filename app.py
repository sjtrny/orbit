#!/usr/bin/env python3
"""Orbit: read-only disk inspection, with atomic SQLite scan snapshots."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.server
import json
import logging
import mimetypes
import os
from pathlib import Path
import queue
import re
import signal
import socket
import stat
import threading
import time
from urllib.parse import parse_qs, urlsplit

STATIC = Path(__file__).parent / "static"
LOG = logging.getLogger("orbit")
SCHEMA = """
CREATE TABLE nodes (
 id INTEGER PRIMARY KEY, parent INTEGER, name TEXT NOT NULL, path TEXT NOT NULL,
 kind TEXT NOT NULL, size INTEGER NOT NULL, apparent INTEGER NOT NULL,
 files INTEGER NOT NULL DEFAULT 0, dirs INTEGER NOT NULL DEFAULT 0,
 mtime REAL NOT NULL, issue TEXT, hardlink INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def connect_db(*args, **kwargs):
    # SQLite caches temporary-directory environment values at initialization.
    # Import only after main() has prepared the persistent scratch directory.
    import sqlite3
    return sqlite3.connect(*args, **kwargs)


def clean_text(value):
    return os.fsdecode(value).encode("utf-8", "replace").decode("utf-8")


VIRTUAL_FILESYSTEMS = {
    "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs",
    "debugfs", "devpts", "devtmpfs", "efivarfs", "fusectl", "hugetlbfs",
    "mqueue", "nsfs", "overlay", "proc", "pstore", "ramfs", "securityfs",
    "selinuxfs", "squashfs", "sysfs", "tmpfs", "tracefs",
}
MOUNT_ESCAPES = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}


def decode_mount_field(value):
    return re.sub(r"\\(040|011|012|134)", lambda match: MOUNT_ESCAPES[match.group(1)], value)


def parse_mountinfo(text):
    mounts = []
    for line in text.splitlines():
        try:
            left, right = line.split(" - ", 1)
            fields = left.split()
            details = right.split()
            mounts.append({
                "major_minor": fields[2],
                "root": decode_mount_field(fields[3]),
                "mountpoint": decode_mount_field(fields[4]),
                "options": fields[5].split(","),
                "filesystem": details[0],
                "source": decode_mount_field(details[1]),
            })
        except (IndexError, ValueError):
            LOG.warning("Ignoring malformed mountinfo entry: %s", line)
    return mounts


def path_is_within(path, parent):
    parent = parent.rstrip("/") or "/"
    return path == parent or (path.startswith("/") if parent == "/" else path.startswith(parent + "/"))


def discovered_id(mountpoint, used):
    base = "system" if mountpoint == "/" else re.sub(r"[^a-zA-Z0-9_-]+", "-", mountpoint.rsplit("/", 1)[-1]).strip("-")
    base = base or "mount"
    candidate = base
    if candidate in used:
        candidate = f"{base}-{hashlib.sha256(mountpoint.encode()).hexdigest()[:8]}"
    used.add(candidate)
    return candidate


def discover_config(host_root="/host", mountinfo_path="/proc/self/mountinfo", data_path="/data"):
    """Discover useful storage mounts already visible below the read-only host bind."""
    host_root = os.path.abspath(host_root)
    data_path = os.path.abspath(data_path)
    mounts = parse_mountinfo(Path(mountinfo_path).read_text())
    visible = [entry for entry in mounts if path_is_within(entry["mountpoint"], host_root)]
    root = next((entry for entry in visible if entry["mountpoint"] == host_root), None)
    if root is None:
        raise ValueError(f"Host root {host_root} is not a visible mount point.")
    writable = next((entry["mountpoint"] for entry in visible if "ro" not in entry["options"]), None)
    if writable:
        raise ValueError(f"Host root must be recursively read-only; {writable} is writable.")

    selected = []
    seen = set()
    for entry in sorted(visible, key=lambda item: (item["mountpoint"].count("/"), item["mountpoint"])):
        if entry is not root and entry["filesystem"] in VIRTUAL_FILESYSTEMS:
            continue
        if entry is not root and any(
            path_is_within(entry["mountpoint"], parent["mountpoint"])
            and entry["major_minor"] == parent["major_minor"]
            and entry["filesystem"] == parent["filesystem"]
            for parent in selected
        ):
            continue
        identity = (entry["major_minor"], entry["root"], entry["filesystem"])
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(entry)

    data_mount = next((entry for entry in mounts if entry["mountpoint"] == data_path), None)
    used_ids = set()
    volumes = []
    for entry in selected:
        relative = os.path.relpath(entry["mountpoint"], host_root)
        display_mount = "/" if relative == "." else "/" + relative
        excluded = {
            os.path.relpath(other["mountpoint"], entry["mountpoint"])
            for other in visible
            if other is not entry and path_is_within(other["mountpoint"], entry["mountpoint"])
            and other["major_minor"] == entry["major_minor"]
        }
        if display_mount == "/":
            excluded.update(("dev", "proc", "run", "sys", "tmp"))
        if data_mount and data_mount["major_minor"] == entry["major_minor"]:
            data_root = os.path.normpath(data_mount["root"])
            entry_root = os.path.normpath(entry["root"])
            if path_is_within(data_root, entry_root) and data_root != entry_root:
                excluded.add(os.path.relpath(data_root, entry_root))
        elif path_is_within(data_path, entry["mountpoint"]) and data_path != entry["mountpoint"]:
            excluded.add(os.path.relpath(data_path, entry["mountpoint"]))
        volumes.append({
            "id": discovered_id(display_mount, used_ids),
            "name": display_mount if display_mount == "/" else display_mount.rsplit("/", 1)[-1],
            "path": entry["mountpoint"],
            "mount": display_mount,
            "device": entry["source"],
            "type": entry["filesystem"],
            "group": "Disks" if display_mount != "/" and entry["source"].startswith("/dev/") else "File systems",
            "exclude": sorted(path for path in excluded if path not in (".", "")),
        })

    hostname = socket.gethostname()
    try:
        hostname = (Path(host_root) / "etc/hostname").read_text().strip() or hostname
    except OSError:
        pass
    return {"hostname": hostname, "sample_seconds": 60, "scan_hours": 6, "volumes": volumes}


def scan_volume(volume, destination, cancel, progress):
    """Never read file contents, follow symlinks, or cross filesystem boundaries."""
    start = time.time()
    root = os.path.abspath(volume["path"])
    excluded = set(volume.get("exclude", []))
    root_stat = os.stat(root, follow_symlinks=False)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("The scan root must be a real directory, not a symbolic link.")
    connection = connect_db(destination)
    connection.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    pending = {root: 1}
    links = set()
    counts = dict(files=0, directories=0, bytes=0, skipped=0, excluded=0, hardlinks=0)
    errors = []
    batch = []
    next_id = 2
    last_progress = 0
    insert_sql = "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    connection.execute(insert_sql, (1, None, volume["name"], "", "directory",
                                   root_stat.st_blocks * 512, 0, 0, 0, root_stat.st_mtime, None, 0))

    def report(force=False):
        nonlocal last_progress
        if force or time.monotonic() - last_progress > 0.25:
            progress({**counts, "elapsed": round(time.time() - start, 1)})
            last_progress = time.monotonic()

    def flush():
        if batch:
            connection.executemany(insert_sql, batch)
            batch.clear()

    def failure(error):
        counts["skipped"] += 1
        relative = os.path.relpath(error.filename or root, root)
        if len(errors) < 30:
            errors.append({"path": clean_text(relative), "reason": error.strerror or str(error)})
        node_id = pending.get(error.filename)
        if node_id:
            flush()
            connection.execute("UPDATE nodes SET issue=? WHERE id=?", (error.strerror, node_id))

    try:
        for directory, directories, files, fd in os.fwalk(root, topdown=True, onerror=failure,
                                                         follow_symlinks=False):
            if cancel.is_set():
                raise InterruptedError("Scan cancelled")
            parent = pending.pop(directory)
            counts["directories"] += 1
            own_size = own_apparent = own_files = 0
            for name in list(directories) + files:
                if cancel.is_set():
                    raise InterruptedError("Scan cancelled")
                full = os.path.join(directory, name)
                relative = os.path.relpath(full, root)
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError as error:
                    error.filename = full
                    failure(error)
                    if name in directories:
                        directories.remove(name)
                    continue
                is_directory = stat.S_ISDIR(info.st_mode)
                is_link = stat.S_ISLNK(info.st_mode)
                kind = "directory" if is_directory else "symlink" if is_link else "file" if stat.S_ISREG(info.st_mode) else "special"
                issue = None
                duplicate = False
                size = info.st_blocks * 512
                apparent = info.st_size if kind in ("file", "symlink") else 0
                if is_directory:
                    if info.st_dev != root_stat.st_dev or relative in excluded:
                        directories.remove(name)
                        kind, size, issue = "excluded", 0, "Separate mount or excluded directory"
                        counts["excluded"] += 1
                    else:
                        pending[full] = next_id
                else:
                    # fwalk can list symlinks to directories in dirnames.
                    if name in directories:
                        directories.remove(name)
                    if kind == "file" and info.st_nlink > 1:
                        identity = (info.st_dev, info.st_ino)
                        duplicate = identity in links
                        links.add(identity)
                        if duplicate:
                            size = apparent = 0
                            counts["hardlinks"] += 1
                    own_size += size
                    own_apparent += apparent
                    own_files += 1
                    counts["files"] += 1
                    counts["bytes"] += size
                batch.append((next_id, parent, clean_text(name), clean_text(relative), kind, size,
                              apparent, 0 if is_directory else 1, 0, info.st_mtime, issue, int(duplicate)))
                next_id += 1
                if len(batch) >= 2000:
                    flush()
                report()
            flush()
            connection.execute("UPDATE nodes SET size=size+?, apparent=?, files=? WHERE id=?",
                               (own_size, own_apparent, own_files, parent))
            report()
        flush()
        connection.commit()
        # IDs are parent-before-child. Fold directory totals bottom-up without
        # keeping the full file tree in RAM, even on multi-million-file disks.
        progress({**counts, "phase": "Summarising", "elapsed": round(time.time() - start, 1)})
        cursor = connection.execute("SELECT id,parent FROM nodes WHERE kind='directory' ORDER BY id DESC")
        for node_id, parent_id in cursor:
            if cancel.is_set():
                raise InterruptedError("Scan cancelled")
            if parent_id is not None:
                connection.execute("""UPDATE nodes SET
                  size=size+(SELECT size FROM nodes WHERE id=?),
                  apparent=apparent+(SELECT apparent FROM nodes WHERE id=?),
                  files=files+(SELECT files FROM nodes WHERE id=?),
                  dirs=dirs+1+(SELECT dirs FROM nodes WHERE id=?) WHERE id=?""",
                                   (node_id, node_id, node_id, node_id, parent_id))
        connection.executescript("""CREATE INDEX nodes_parent ON nodes(parent,size DESC);
          CREATE INDEX nodes_largest ON nodes(kind,size DESC);
          CREATE INDEX nodes_apparent ON nodes(kind,apparent DESC);""")
        result = {**counts, "started_at": start, "completed_at": time.time(),
                  "duration": round(time.time() - start, 2), "errors": errors}
        connection.execute("INSERT INTO metadata VALUES ('scan',?)", (json.dumps(result),))
        connection.commit()
        report(True)
        return result
    finally:
        connection.close()


class Monitor:
    def __init__(self, config, data_dir):
        self.config = config
        configured = []
        for source in config["volumes"]:
            target = dict(source)
            if "id" not in target or "path" not in target:
                raise ValueError("Each scan target needs an ID and directory path.")
            target.setdefault("mount", target["path"])
            target.setdefault("name", target["mount"])
            target.setdefault("group", "File systems")
            configured.append(target)
        self.volumes = {target["id"]: target for target in configured}
        if not self.volumes or len(self.volumes) != len(configured):
            raise ValueError("Configure at least one scan target, with unique IDs.")
        for key in self.volumes:
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
                raise ValueError("Target IDs may only use letters, digits, underscores and hyphens.")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.queue = queue.Queue()
        self.stop = threading.Event()
        self.jobs = {}
        self.capacity = []
        self.sampled_at = None
        self.capacity_error = None
        self.history_path = self.data_dir / "history.sqlite"
        with connect_db(self.history_path) as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS samples (
              volume TEXT, time REAL, total INTEGER, used INTEGER, available INTEGER,
              PRIMARY KEY(volume,time));
              CREATE INDEX IF NOT EXISTS samples_time ON samples(time);""")
        self.worker = threading.Thread(target=self.work, name="scanner", daemon=True)
        self.sampler = threading.Thread(target=self.sample_loop, name="capacity", daemon=True)

    def snapshot(self, volume_id):
        return self.data_dir / f"{volume_id}.sqlite"

    def connect_snapshot(self, volume_id):
        if volume_id not in self.volumes:
            raise KeyError("Unknown target")
        path = self.snapshot(volume_id)
        if not path.exists():
            raise FileNotFoundError("This target has not been scanned yet")
        db = connect_db(f"file:{path}?mode=ro", uri=True, timeout=10)
        db.row_factory = lambda cursor, row: dict(zip((column[0] for column in cursor.description), row))
        return db

    def scan_metadata(self, volume_id):
        try:
            with contextlib.closing(self.connect_snapshot(volume_id)) as db:
                return json.loads(db.execute("SELECT value FROM metadata WHERE key='scan'").fetchone()['value'])
        except FileNotFoundError:
            return None

    def start(self):
        self.sample()
        self.worker.start()
        self.sampler.start()
        for volume_id in self.volumes:
            if not self.snapshot(volume_id).exists():
                self.enqueue(volume_id)

    def enqueue(self, volume_id):
        if volume_id not in self.volumes:
            raise KeyError("Unknown target")
        with self.lock:
            previous = self.jobs.get(volume_id, {})
            if previous.get("status") in ("queued", "scanning", "cancelling"):
                return self.public_job(previous)
            job = {"status": "queued", "queued_at": time.time(), "cancel": threading.Event(),
                   "files": 0, "directories": 0, "bytes": 0, "skipped": 0}
            self.jobs[volume_id] = job
            self.queue.put((volume_id, job))
            return self.public_job(job)

    @staticmethod
    def public_job(job):
        return {k: v for k, v in job.items() if k != "cancel"}

    def cancel(self, volume_id):
        if volume_id not in self.volumes:
            raise KeyError("Unknown target")
        with self.lock:
            job = self.jobs.get(volume_id)
            if job and job["status"] in ("queued", "scanning", "cancelling"):
                job["cancel"].set()
                job["status"] = "cancelled" if job["status"] == "queued" else "cancelling"
            return self.public_job(job) if job else {"status": "idle"}

    def work(self):
        while not self.stop.is_set():
            try:
                volume_id, job = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            temp = self.data_dir / f"{volume_id}.scanning.sqlite"
            try:
                if job["cancel"].is_set():
                    continue
                with self.lock:
                    job.update(status="scanning", started_at=time.time(), phase="Scanning")
                temp.unlink(missing_ok=True)
                def progress(values):
                    with self.lock:
                        job.update(values)
                result = scan_volume(self.volumes[volume_id], temp, job["cancel"], progress)
                with self.lock:
                    if job["cancel"].is_set():
                        raise InterruptedError("Scan cancelled")
                    os.replace(temp, self.snapshot(volume_id))
                    job.update(status="complete", completed_at=result["completed_at"])
                LOG.info("Scanned %s: %s files in %.1fs", volume_id, result["files"], result["duration"])
            except InterruptedError:
                with self.lock:
                    job["status"] = "cancelled"
            except Exception as error:
                LOG.exception("Scan failed for %s", volume_id)
                with self.lock:
                    job.update(status="error", error=str(error))
            finally:
                temp.unlink(missing_ok=True)
                self.queue.task_done()

    def sample(self):
        timestamp = time.time()
        volumes = []
        rows = []
        for volume_id, config in self.volumes.items():
            item = {k: v for k, v in config.items() if k not in ("path", "exclude")}
            try:
                s = os.statvfs(config["path"])
                total = s.f_blocks * s.f_frsize
                free = s.f_bfree * s.f_frsize
                available = s.f_bavail * s.f_frsize
                used = max(0, total - free)
                reserved = max(0, free - available)
                item.update(total=total, used=used, available=available, reserved=reserved,
                            percent=round(100 * used / max(1, used + available), 1), online=True)
                rows.append((volume_id, timestamp, total, used, available))
            except OSError as error:
                item.update(online=False, error=error.strerror)
            volumes.append(item)
        with connect_db(self.history_path, timeout=20) as db:
            db.executemany("INSERT INTO samples VALUES (?,?,?,?,?)", rows)
            db.execute("DELETE FROM samples WHERE time < ?", (timestamp - 30 * 86400,))
        with self.lock:
            self.capacity = volumes
            self.sampled_at = timestamp
            self.capacity_error = None

    def sample_loop(self):
        interval = max(10, int(self.config.get("sample_seconds", 60)))
        scan_hours = max(0, float(self.config.get("scan_hours", 6)))
        while not self.stop.wait(interval):
            try:
                self.sample()
                if scan_hours:
                    for volume_id in self.volumes:
                        meta = self.scan_metadata(volume_id)
                        with self.lock:
                            job = self.jobs.get(volume_id, {})
                            retry_after = max(meta["completed_at"] if meta else 0, job.get("queued_at", 0))
                        if time.time() - retry_after >= scan_hours * 3600:
                            self.enqueue(volume_id)
            except Exception as error:
                with self.lock:
                    self.capacity_error = str(error)
                LOG.exception("Capacity sample failed")

    def status(self):
        with self.lock:
            volumes = [dict(v) for v in self.capacity]
            jobs = {k: self.public_job(v) for k, v in self.jobs.items()}
        for v in volumes:
            v["scan"] = self.scan_metadata(v["id"])
            v["job"] = jobs.get(v["id"], {"status": "idle"})
        return {"hostname": self.config.get("hostname", socket.gethostname()),
                "sampled_at": self.sampled_at, "capacity_error": self.capacity_error,
                "scan_hours": self.config.get("scan_hours", 6), "sample_seconds": self.config.get("sample_seconds", 60),
                "volumes": volumes}

    @staticmethod
    def node(row):
        return dict(row) if row is not None else None

    def tree(self, volume_id, node_id, metric="size", offset=0, limit=100, depth=3):
        metric = "apparent" if metric == "apparent" else "size"
        with contextlib.closing(self.connect_snapshot(volume_id)) as db:
            db.execute("BEGIN")  # Keep one coherent snapshot throughout this request.
            current = self.node(db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone())
            if not current:
                raise KeyError("Folder not found")
            ancestors = []
            parent = current["parent"]
            while parent:
                ancestor = self.node(db.execute("SELECT * FROM nodes WHERE id=?", (parent,)).fetchone())
                ancestors.append({k: ancestor[k] for k in ("id", "name", "path")})
                parent = ancestor["parent"]
            count = db.execute("SELECT COUNT(*) AS n FROM nodes WHERE parent=?", (node_id,)).fetchone()['n']
            children = [dict(r) for r in db.execute(f"SELECT * FROM nodes WHERE parent=? ORDER BY {metric} DESC,name LIMIT ? OFFSET ?",
                                                   (node_id, limit, offset))]
            budget = [500]
            def branch(item, remaining):
                if remaining <= 0 or item["kind"] != "directory" or budget[0] <= 0:
                    return item
                rows = db.execute(f"SELECT * FROM nodes WHERE parent=? AND {metric}>0 ORDER BY {metric} DESC,name LIMIT ?",
                                  (item["id"], min(24, budget[0]))).fetchall()
                # Reserve siblings before expanding descendants, so a deep
                # first branch cannot hide the other large top-level folders.
                budget[0] -= len(rows)
                nodes = []
                for row in rows:
                    nodes.append(branch(dict(row), remaining - 1))
                shown = sum(n[metric] for n in nodes)
                rest = max(0, item[metric] - shown)
                if rest:
                    nodes.append({"id": f"other-{item['id']}", "name": "Other items & directory data",
                                  "size": rest if metric == "size" else 0, "apparent": rest if metric == "apparent" else 0,
                                  "kind": "aggregate", "parent": item["id"], "path": item["path"]})
                item["children"] = nodes
                return item
            chart = branch(dict(current), depth)
            return {"node": current, "ancestors": list(reversed(ancestors)), "children": children,
                    "count": count, "offset": offset, "chart": chart, "scan": self.scan_metadata_from(db)}

    @staticmethod
    def scan_metadata_from(db):
        return json.loads(db.execute("SELECT value FROM metadata WHERE key='scan'").fetchone()['value'])

    def search(self, volume_id, query="", metric="size", limit=100):
        metric = "apparent" if metric == "apparent" else "size"
        with contextlib.closing(self.connect_snapshot(volume_id)) as db:
            db.execute("BEGIN")
            if query:
                escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = db.execute(f"SELECT * FROM nodes WHERE name LIKE ? ESCAPE '\\' ORDER BY {metric} DESC LIMIT ?",
                                  (f"%{escaped}%", limit))
            else:
                rows = db.execute(f"SELECT * FROM nodes WHERE kind='file' ORDER BY {metric} DESC LIMIT ?", (limit,))
            return {"items": [dict(r) for r in rows], "limit": limit, "query": query, "scan": self.scan_metadata_from(db)}

    def history(self, volume_id):
        if volume_id not in self.volumes:
            raise KeyError("Unknown target")
        with connect_db(self.history_path, timeout=10) as db:
            rows = db.execute("""SELECT MAX(time), AVG(used), AVG(available), MAX(total)
                FROM samples WHERE volume=? AND time>? GROUP BY CAST(time/900 AS INTEGER)
                ORDER BY MAX(time)""", (volume_id, time.time() - 86400)).fetchall()
        return {"points": [{"time": r[0], "used": round(r[1]), "available": round(r[2]), "total": r[3]} for r in rows]}

    def close(self):
        self.stop.set()
        with self.lock:
            for job in self.jobs.values():
                job["cancel"].set()
        if self.worker.is_alive():
            self.worker.join(timeout=10)
        if self.sampler.is_alive():
            self.sampler.join(timeout=5)


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Orbit"

    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            LOG.info(fmt, *args)

    def respond(self, status, payload, content_type="application/json; charset=utf-8"):
        data = json.dumps(payload, ensure_ascii=True).encode() if not isinstance(payload, bytes) else payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            self.route_get()
        except (ValueError, OverflowError):
            self.respond(400, {"error": "Invalid request parameter"})
        except (KeyError, FileNotFoundError) as error:
            self.respond(404, {"error": str(error).strip("'")})
        except Exception:
            LOG.exception("GET failed")
            self.respond(500, {"error": "Could not load storage data"})

    def route_get(self):
        url = urlsplit(self.path)
        params = parse_qs(url.query)
        def arg(name, default=""):
            return params.get(name, [default])[0]
        def bounded(name, default, low, high):
            return min(high, max(low, int(arg(name, str(default)))))
        monitor = self.server.monitor
        if url.path == "/health":
            return self.respond(200, {"status": "ok"})
        if url.path == "/api/status":
            return self.respond(200, monitor.status())
        if url.path == "/api/tree":
            return self.respond(200, monitor.tree(arg("volume"), bounded("node", 1, 1, 2**63-1), arg("metric"),
                                                   bounded("offset", 0, 0, 10**9), bounded("limit", 100, 1, 500), bounded("depth", 3, 1, 4)))
        if url.path == "/api/search":
            return self.respond(200, monitor.search(arg("volume"), arg("q")[:200], arg("metric"), bounded("limit", 100, 1, 500)))
        if url.path == "/api/history":
            return self.respond(200, monitor.history(arg("volume")))
        assets = {
            "/": "index.html",
            "/app.js": "app.js",
            "/bootstrap.min.css": "bootstrap.min.css",
            "/orbit.css": "orbit.css",
            "/favicon.svg": "favicon.svg",
        }
        if url.path in assets:
            path = STATIC / assets[url.path]
            return self.respond(200, path.read_bytes(), mimetypes.guess_type(path)[0] + "; charset=utf-8")
        self.respond(404, {"error": "Not found"})

    def do_POST(self):
        try:
            # JSON plus a non-simple custom header prevents cross-origin form requests.
            if self.headers.get("X-Orbit-Request") != "1" or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond(403, {"error": "Use the Orbit interface to start a scan"})
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.respond(403, {"error": "Cross-origin requests are not accepted"})
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 4096:
                return self.respond(413, {"error": "Request too large"})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict) or not isinstance(body.get("volume"), str):
                return self.respond(400, {"error": "Choose a target"})
            path = urlsplit(self.path).path
            if path == "/api/scan":
                return self.respond(202, self.server.monitor.enqueue(body["volume"]))
            if path == "/api/cancel":
                return self.respond(200, self.server.monitor.cancel(body["volume"]))
            self.respond(404, {"error": "Not found"})
        except (ValueError, TypeError, KeyError) as error:
            self.respond(400, {"error": str(error)})
        except Exception:
            LOG.exception("POST failed")
            self.respond(500, {"error": "Could not update scan"})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("ORBIT_CONFIG"),
                        help="Optional JSON target override; mounts are discovered when omitted")
    parser.add_argument("--host-root", default=os.environ.get("ORBIT_HOST_ROOT", "/host"))
    parser.add_argument("--data", default=os.environ.get("ORBIT_DATA", "./data"))
    parser.add_argument("--host", default=os.environ.get("ORBIT_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ORBIT_PORT", "8080")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Large index builds spill sorted pages to disk. Keep that scratch space
    # beside the snapshots, not in a container's small /tmp tmpfs.
    scratch = Path(args.data).resolve() / 'tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ['SQLITE_TMPDIR'] = str(scratch)
    if args.config:
        with open(args.config) as file:
            config = json.load(file)
        LOG.info("Loaded scan targets from %s", args.config)
    else:
        host_root = args.host_root
        config = discover_config(host_root=host_root, data_path=args.data)
        LOG.info("Discovered %s mounted filesystems below %s", len(config["volumes"]), host_root)
    monitor = Monitor(config, args.data)
    server = Server((args.host, args.port), Handler)
    server.monitor = monitor
    def stop_server(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    try:
        monitor.start()
        LOG.info("Orbit listening on %s:%s", args.host, args.port)
        server.serve_forever(poll_interval=0.25)
    finally:
        monitor.close()
        server.server_close()


if __name__ == "__main__":
    main()
