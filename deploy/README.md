# `deploy/`

Versioned server-side configuration. Files here live at fixed paths on the
Hetzner VM (`/opt/...`); they aren't imported by the application, but they
own behavior — when they drift, things break silently.

## Files

| File | Installed at | Owner | Notes |
|---|---|---|---|
| `alif-update-material.sh` | `/opt/alif-update-material.sh` | system crontab | 3-hourly material cron wrapper. Sets env vars that keep the frequency-core intake supply chain running. |

## How to update

1. Edit the file in this repo and commit.
2. `scp deploy/<file> alif:/opt/<file>`
3. `ssh alif chmod +x /opt/<file>` if it's a script.
4. Optional verify pass: `ssh alif /opt/<file>` and inspect `/var/log/<file basename>.log`.

Don't edit the server copy directly. The 2026-05-13 frequency-core drought
incident (see `research/experiment-log.md`) was diagnosed in part because we
had to re-derive what the server wrapper looked like — that's the kind of
forensic cost we're paying for not versioning these.

## Why not Ansible / Terraform / Docker?

Personal project, single VM, single user. The deploy footprint is small
enough that `scp` + a checked-in source-of-truth covers the failure modes
that matter (drift, lost edits after reprovision). If the surface grows,
revisit.
