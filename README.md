# Orbit

A minimal, read-only disk usage viewer for a Linux server.

**Live:** <http://192.168.20.10:18008/>

## Use

1. Choose a mounted file system or disk.
2. Read its live used and free space.
3. Click a map segment or folder to drill down.
4. Click the center, back arrow, or breadcrumb to move up.
5. Click **Scan** to refresh the file map. A cancelled or failed scan does not replace the last complete map.

That is the whole interface. The app cannot edit, move, open, or delete files.

The interface uses a locally vendored copy of Bootstrap 5.3.8. It does not
load CSS or JavaScript from a CDN. `static/orbit.css` contains only small
integration rules for hidden states, SVG icons, and disk-map geometry.
The Bootstrap license is included in `static/BOOTSTRAP-LICENSE.txt`.

## Run with Docker

Orbit requires a Linux host because it reads filesystem allocation metadata.
The included Compose service binds only to loopback, drops all capabilities,
uses a read-only container root, and mounts the host filesystem read-only.

```bash
cp volumes.example.json volumes.json
docker compose up --build --detach
curl http://127.0.0.1:8080/health
```

Open <http://127.0.0.1:8080/>. The example configuration monitors `/`. Edit
the ignored `volumes.json` to add mounted filesystems or disks. The recursive
`/host` bind makes host mount points available at the corresponding path under
`/host`; every configured target is still scanned independently and scans never
cross filesystem boundaries.

The image contains no host-specific configuration. Compose mounts
`volumes.json` at runtime, and the named `orbit-data` volume retains completed
scan snapshots between container replacements. Set `ORBIT_LISTEN_ADDRESS` or
`ORBIT_PORT` in an ignored `.env` file if the loopback defaults need changing.
Orbit has no application login, so do not expose it publicly without an
authenticated reverse proxy.

Each target needs an `id` and container-visible directory `path`. `name` and
`mount` default to `path`; `group` defaults to `File systems`. Optional fields
include `device`, `type`, and exact root-relative directory paths in `exclude`.
A disk must have a mounted filesystem; Orbit does not inspect raw block devices.

## Run from source

Requires Python 3.12 or later. Change each configured `path` from its container
path to the corresponding local path, then run:

```bash
python3 app.py --config volumes.json --data ./data --host 127.0.0.1 --port 8080
```

## Deploy on this host

The current rootless Docker deployment monitors 11 explicit mounts and listens only on the private host address:

```bash
docker stop --timeout 15 orbit
docker rename orbit orbit-before-update
python3 deploy.py
```

`deploy.py` refuses to replace an existing container. Use a unique rollback
name if needed. It builds the same generic image, copies the ignored host
configuration into the `orbit-config` Docker volume, and mounts that volume
read-only. Scan snapshots live in Docker volume `orbit-data` and survive
container replacement.

The container has a read-only root filesystem, no Linux capabilities, no Docker socket, and read-only host binds. It has no application login, so do not expose it publicly without an authenticated reverse proxy.

## Accounting

- Capacity is read live with `statvfs`.
- The map counts allocated blocks (`st_blocks × 512`), including directory blocks.
- Hard links count once per scan. Symlinks are listed but never followed.
- Scans do not cross filesystem boundaries and report unreadable or disappearing entries.
- Filesystem metadata, protected files, deleted-but-open files, and changes during a scan can make indexed space differ from live used space.
- Every configured target is reported independently. Orbit does not add capacities together or guess whether two mounts overlap.
- A completed SQLite snapshot is published atomically; the previous snapshot stays available while scanning.

## Check

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
node tests/browser.cjs
```

See [artifacts/README.md](artifacts/README.md) for the live verification record.
