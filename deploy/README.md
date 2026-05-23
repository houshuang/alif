# `deploy/`

Versioned server-side configuration. Files here live at fixed paths on the
Hetzner VM (`/opt/...`); they aren't imported by the application, but they
own behavior — when they drift, things break silently.

## Files

| File | Installed at | Owner | Notes |
|---|---|---|---|
| `alif-update-material.sh` | `/opt/alif-update-material.sh` | system crontab | 3-hourly material cron wrapper. Sets env vars that keep the frequency-core intake supply chain running. |

## How to update

1. Edit the file in this repo and commit to `main`.
2. Run the relevant Git deploy script (`scripts/deploy.sh` for Alif,
   `polyglot/deploy/deploy-polyglot.sh` for Polyglot).
3. The deploy script pulls `origin/main` on the VM and links `/opt/<file>` to
   the checked-out repo copy. Do not `scp` deploy files to production.
4. Optional verify pass: `ssh alif /opt/<file>` and inspect `/var/log/<file basename>.log`.

Don't edit the server copy directly. The 2026-05-13 frequency-core drought
incident (see `research/experiment-log.md`) was diagnosed in part because we
had to re-derive what the server wrapper looked like. The production wrapper
should now always be a symlink to the Git checkout so the deployed state is
recoverable from `main`.

## Why not Ansible / Terraform / Docker?

Personal project, single VM, single user. The deploy footprint is small enough
that Git pull + symlinked versioned wrappers covers the failure modes that
matter (drift, lost edits after reprovision). If the surface grows, revisit.
