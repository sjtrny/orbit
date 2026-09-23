# Orbit

A small, self-hosted disk usage viewer for Linux.

## Features

- Live used and free capacity.
- Interactive directory map and folder navigation.
- Independent targets for `/`, mounted filesystems, and mounted disks.
- Atomic scan snapshots: a cancelled or failed scan keeps the last complete map.
- Read-only operation with no file editing, moving, or deletion.

## Run

```bash
cp volumes.example.json volumes.json
docker compose up -d --build
```

Open <http://localhost:8080/>. The example monitors `/` and listens only on
localhost.

Edit `volumes.json` to add targets. Each target needs an `id` and a
container-visible directory `path`. Host paths are available below `/host`, so
the host mount `/mnt/archive` is `/host/mnt/archive` in the container. A disk
must have a mounted filesystem; Orbit does not inspect raw block devices.

Set `ORBIT_LISTEN_ADDRESS` or `ORBIT_PORT` in `.env` to change the listener.
Orbit has no login, so use an authenticated reverse proxy before exposing it to
a network.

## State and safety

Completed maps persist in the `orbit-data` Docker volume. The container root
and host mounts are read-only, all Linux capabilities are dropped, and scans do
not follow symlinks or cross filesystem boundaries.

The map counts allocated blocks and counts hard links once. Unreadable entries
are reported. Live filesystem capacity can differ from a completed scan because
of metadata, protected files, deleted-but-open files, or later changes.

## Check

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
node tests/browser.cjs
```
