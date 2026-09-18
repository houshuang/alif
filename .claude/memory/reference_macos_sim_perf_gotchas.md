---
name: macos-sim-perf-gotchas
description: "Local heavy-compute gotchas — background shells run darwinbg/niced (E-cores), SQLite page cache must exceed DB size, sample(1) beats theorizing, launchctl submit for session-surviving jobs"
metadata: 
  node_type: memory
  type: reference
  originSessionId: f59db1f0-915e-4a47-aa30-7b0a9dc3d2fd
---

Hard-won during the 2026-07-14 volume-sweep session (cost ~3 hours of misdiagnosis):

- **Claude Code background shells run niced (nice 5, darwinbg)** on macOS — CPU-heavy children get scheduled onto efficiency cores and look mysteriously 2-3× slow. `taskpolicy -c` can only clamp DOWN; to get standard priority spawn via `launchctl submit` (also survives session end — but NOT machine sleep/reboot; overnight jobs on a laptop need `caffeinate` or acceptance of loss).
- **SQLite page cache must exceed DB file size** for scan-heavy workloads: cutting `cache_size` from 256MB to 64MB on a 117MB DB caused permanent thrash (10× slowdown; processes look "stuck" at low %CPU). Also: sim/analysis engines built by `sessionmaker(bind=engine)` get NO pragmas — alif's pragma listener is bound to `app.database.engine` only.
- **`sample <pid>` (macOS built-in) beats theorizing** about why a process is slow — it showed "0.1% CPU stuck" was actually CPU-pinned in an unindexed SQLite B-tree scan. py-spy needs root on macOS.
- **`expire_on_commit=False`** for single-threaded sim/analysis sessions: alif's `build_session` commits mid-call, and default expiry turns every later ORM attribute access into a single-row refresh SELECT (~4k extra queries/call).
- Kill patterns: `pkill -f <script>` also kills same-pattern profiling/watcher jobs; and killing a launcher's children makes its `wait` return and start the NEXT batch — kill the launcher shell first.

Related fix committed to repo: [[calculation-before-simulation]]; sim harness lessons documented in `research/analysis-2026-07-14-momo-readiness-volume-sweep.md`.
