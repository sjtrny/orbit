# Orbit disk usage — TODO

## Outcome and context

- **Desired outcome:** A DaisyDisk-style web app that monitors the physical host's storage, with real capacity data and an interactive directory map.
- **Working location:** `/workspaces/orbit/`.
- **Constraints:** Read-only host access. Do not alter existing host services or delete monitored files.

## In progress

None.

## Done

- [x] DISK-012 — Publish Orbit to GHCR automatically on updates to `main` (2026-11-08).
  - Added the [publishing workflow](.github/workflows/publish.yml) using `GITHUB_TOKEN`, with backend checks and Linux AMD64/ARM64 images tagged `latest` and `sha-<commit>`.
  - The public [GHCR package](https://github.com/users/sjtrny/packages/container/package/orbit) is linked to the source repository. The README Compose example now pulls it directly.
  - [Publication and verification passed](https://github.com/sjtrny/orbit/actions/runs/42536823651): 15 backend checks before publishing and inside each published architecture. Anonymous registry access and an image pull with empty Docker credentials succeeded.

- [x] DISK-011 — Simplify the implementation by removing one-off abstractions and unused backend features (2026-09-23).
  - Replaced the configuration wrapper with a target dictionary and removed the one-use HTTP server subclass.
  - Removed unused search/history APIs, capacity-history writes, variable tree modes, redundant response fields, and duplicate frontend scan state.
  - Reduced the implementation and tests by 110 lines, redeployed it, and preserved all 11 snapshots without queuing scans.

- [x] DISK-010 — Discover mounted filesystems automatically and redeploy without a volume configuration file (2026-09-23).
  - Replaced the per-target JSON file and bind mounts with one read-only host-root bind and `/proc/self/mountinfo` discovery.
  - Filtered virtual and duplicate bind mounts, kept stable target IDs, excluded Orbit's data volume, and rejected any writable host submount.
  - Redeployed the healthy rootless container with all 11 existing snapshots preserved and no scan jobs queued.

- [x] DISK-009 — Put the minimal Compose definition directly in the README (2026-09-23).
  - Replaced the separate `compose.yaml` with a terse, validated definition in the Run section.
  - Kept the read-only root and host mount, dropped capabilities, loopback listener, and persistent data volume.

- [x] DISK-008 — Shorten the public README to match the concise style of the reference repositories (2026-09-23).
  - Reduced the README from 597 to 237 words and made it Docker-first.
  - Retained only features, startup, target configuration, state/safety, and check commands.
  - Removed private deployment details and validated the documented Compose configuration.

- [x] DISK-007 — Rename the project folder and repository from `disk-usage` to `orbit` (2026-09-23).
  - Moved the independent Git repository to `/workspaces/orbit` and updated all workspace links and service notes.
  - Verified Compose rendering, the healthy live container, and all seven browser interaction groups from the new path.

- [x] DISK-006 — Create the project Git repository and portable Docker/Compose packaging (2026-09-23).
  - Added hardened Docker packaging, generic example configuration, persistent data volume, and runtime-only configuration injection.
  - The image contains no host-specific mount list; the host helper uses a separate read-only `orbit-config` volume.
  - Initialized the independent repository on `main`; ignored host configuration, scan data, caches, and generated browser artifacts.
  - Verified the image build, Compose rendering, 14 backend checks, seven live browser groups, all 11 read-only host binds, and snapshot persistence.

- [x] DISK-005 — Remove the ambiguous Live connection badge (2026-09-20).
  - Connection failures still appear as an alert and continue retrying automatically.
  - Verified the live desktop/mobile interface and all seven interaction groups.

- [x] DISK-004 — Replace app-wide custom styling with locally vendored Bootstrap 5.3.8 (2026-09-20).
  - Forms, buttons, badges, alerts, cards, lists, typography, spacing, and responsive layout now use Bootstrap classes.
  - Bootstrap CSS and its MIT license are local assets; Orbit has no CDN or Bootstrap JavaScript dependency.
  - Retained only a 2.3 KB integration stylesheet for hidden-state handling, SVG icons, and disk-map geometry.
  - Verified all 14 backend checks and seven live desktop/mobile interaction groups; all 11 host binds remain read-only.

- [x] DISK-003 — Remove pool-specific assumptions and describe every entry as an independent mounted filesystem or disk (2026-09-20).
  - Configuration now accepts independent directory targets with path-based defaults. A target can be `/`, another mounted filesystem, or a mounted disk.
  - Removed pool flags and aggregate capacity. Orbit does not infer whether configured mount targets overlap.
  - The live selector groups this host's entries as `File systems` and `Disks`; no pool-specific label remains.
  - Verified with 14 backend checks and seven live desktop/mobile interaction groups.

- [x] DISK-002 — Reduce Orbit to the minimum useful interface (2026-09-20).
  - Retained only volume selection, live capacity, scan/cancel state, breadcrumbs, the disk map, and the folder list.
  - Removed the sidebar, secondary views, stat-card grid, search UI, activity chart, help dialog, legends, promotional panels, and footer.
  - Verified the live desktop and mobile layouts, volume switching, keyboard drill-down, list navigation, responsive stacking, and absence of browser errors. All prior scan snapshots remained intact.

- [x] DISK-001 — Build and run the disk usage web app (2026-09-20).
  - Live: <http://192.168.20.10:18008/> in rootless Docker container `orbit`.
  - Completed: 11 real host volumes, live capacity, interactive sunburst and folder navigation, allocated/logical sizes, largest-file search, scan queue/cancellation, six-hour rescans, and persistent capacity history.
  - Verified: 13 backend checks; desktop/tablet/mobile browser interactions; actual queued scan cancellation without losing the previous map; all 11 snapshots retained across container replacement. The system scan indexed 2,311,221 files in 78.62 seconds and reported 1,605 inaccessible/disappearing entries instead of claiming full coverage.
  - Read-only host mounts verified. Existing services were not changed.
  - [Operation and deployment](README.md), [verification record](artifacts/README.md), [desktop screenshot](artifacts/desktop.png), [mobile screenshot](artifacts/mobile.png).
