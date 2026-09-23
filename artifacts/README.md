# Orbit verification — 2026-09-23

Live endpoint: <http://192.168.20.10:18008/>.

- `backend-results.txt`: scanner, accounting, snapshot, query, and HTTP checks passed. The current suite has 14 checks, including generic path defaults and independent target reporting.
- `browser-results.json`: the Bootstrap-based live UI passed all seven desktop/mobile interaction groups, with no uncaught browser errors.
- `minimal-desktop.png`, `minimal-mobile.png`: the Bootstrap 5.3.8 interface with actual host data, checked with Playwright and visually inspected. No mock dataset is used.
- `host-verification.json`: capacity and scan metadata for all 11 targets after deployment restart. All completed snapshots survived replacement.
- Additional live check: queue a rescan of `/pools/storage2`, cancel it with the UI, and verify the completed map's timestamp and sunburst are unchanged. Requeued scan then completed successfully.

The rootless `orbit` container is healthy. Its private bind is
`192.168.20.10:18008 → 8080`. All visible host bind/submount entries were
read-only at final inspection; no monitored files were written or deleted.
Container image: `sha256:26134cd773f6ff32a317b3462a9d717bea2b7486981650f003f9b2cb84f281e8`.
Persistent state is in Docker volume `orbit-data`; host configuration is in the
read-only `orbit-config` volume and is not baked into the image. The prior
working build is retained, stopped, as `orbit-before-git-docker`. Bootstrap is
served locally; the removed legacy stylesheet returns 404. Intermediate failed
deployment containers were removed; other services were left unchanged.

The system scan indexed 2,311,221 files and reported 1,605 skipped entries in
78.62 seconds. These are permission/scan gaps, not a claim that all filesystem
used space was attributed. Smaller mounted volumes also report their own gaps.
Each mount target is reported independently; no aggregate total assumes whether
two targets share underlying storage.

The large system scan exposed the 32 MB container `/tmp` limit during SQLite
index construction. SQLite selects its scratch configuration during library
initialization, so `/data/tmp` is now configured before importing SQLite and
also supplied in the image environment. A synthetic 700,000-row wide-key index
and the full real system scan both succeeded with disk-backed scratch space.
