# Orbit verification — 2026-09-23

Live endpoint: <http://192.168.20.10:18008/>.

- `backend-results.txt`: scanner, discovery, accounting, snapshot, query, and HTTP checks passed. The current suite has 15 checks.
- `browser-results.json`: the Bootstrap-based live UI passed all seven desktop/mobile interaction groups, with no uncaught browser errors.
- `minimal-desktop.png`, `minimal-mobile.png`: the Bootstrap 5.3.8 interface with actual host data, checked with Playwright and visually inspected. No mock dataset is used.
- `host-verification.json`: capacity and scan metadata for all 11 targets after deployment restart. All completed snapshots survived replacement.
- Additional live check: queue a rescan of `/pools/storage2`, cancel it with the UI, and verify the completed map's timestamp and sunburst are unchanged. Requeued scan then completed successfully.

The rootless `orbit` container is healthy. Its private bind is
`192.168.20.10:18008 → 8080`. One recursive `/:/host` bind exposes the host
read-only; Orbit discovered all 11 mounted targets and rejected writable host
submounts at startup. No monitored files were written or deleted. Container
image: `sha256:8f9d9ad7db3782dbecfa4e469903c2ef6a8181d0a4b339adbabfce134c94764a`.
Persistent state is in Docker volume `orbit-data`; the current container has no
configuration volume. The prior deployment is retained, stopped, as
`orbit-before-simplify`; the older `orbit-before-auto-config` container and its
legacy `orbit-config` volume also remain available for rollback. Bootstrap is
served locally, and other services were unchanged.

The current system snapshot indexed 2,402,300 files and reported 1,708 skipped
entries in 79.94 seconds. These are permission/scan gaps, not a claim that all
filesystem used space was attributed. Smaller mounted volumes also report their own gaps.
Each mount target is reported independently; no aggregate total assumes whether
two targets share underlying storage.

The large system scan exposed the 32 MB container `/tmp` limit during SQLite
index construction. SQLite selects its scratch configuration during library
initialization, so `/data/tmp` is now configured before importing SQLite and
also supplied in the image environment. A synthetic 700,000-row wide-key index
and the full real system scan both succeeded with disk-backed scratch space.
