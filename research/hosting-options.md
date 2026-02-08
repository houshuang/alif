# Hosting Options for Alif Backend (FastAPI + CAMeL Tools + SQLite)

*Researched: February 2026*

## Requirements Summary

- **Runtime:** Python 3.11+ FastAPI with CAMeL Tools (~300MB model data in memory)
- **Storage:** SQLite database (persistent filesystem required)
- **RAM:** 512MB absolute minimum, 1GB+ recommended (Python + CAMeL Tools + model data)
- **Users:** Single user, no scaling concerns
- **Deploy:** Simple CLI-based deployment (git push or single command)
- **Budget:** Under $5/month ideal, under $10/month acceptable

## Comparison Table

| Provider | Plan | RAM | CPU | Disk | Monthly Cost | Persistent FS | Deploy Method | Cold Start | Notes |
|---|---|---|---|---|---|---|---|---|---|
| **Hetzner Cloud** | CAX11 (ARM) | 4 GB | 2 vCPU | 40 GB | ~$4/mo (EUR 3.79) | Yes (VPS) | SSH/Coolify/Kamal | None (always on) | Best value; EU-only |
| **Hetzner Cloud** | CX22 (x86) | 4 GB | 2 vCPU | 40 GB | ~$4/mo (EUR 3.79) | Yes (VPS) | SSH/Coolify/Kamal | None (always on) | Same price, x86 compatibility |
| **Oracle Cloud** | Always Free ARM | 24 GB | 4 OCPU | 200 GB | **$0** | Yes (VPS) | SSH | None (always on) | Free forever; hard to provision |
| **Fly.io** | shared-cpu-1x, 1GB | 1 GB | 1 shared | 1 GB vol | ~$7-8/mo | Yes (volumes) | `fly deploy` | Possible if stopped | Volume: +$0.15/GB/mo |
| **Railway** | Hobby | ~1 GB | shared | 5 GB vol | ~$10-15/mo | Yes (volumes) | `git push` / CLI | None (always on) | $5 sub + usage; RAM is $10/GB/mo |
| **Render** | Starter | 512 MB | 0.5 | + disk | ~$9-10/mo | Yes (paid disk) | `git push` | Spins down after inactivity | 512MB likely too tight |
| **DigitalOcean** | Basic Droplet | 1 GB | 1 vCPU | 25 GB | $6/mo | Yes (VPS) | SSH/Coolify/Kamal | None (always on) | $4/mo for 512MB (too small) |
| **AWS Lightsail** | Nano (IPv4) | 512 MB | 1 vCPU | 20 GB | $5/mo | Yes (VPS) | SSH | None (always on) | 512MB tight; $10/mo for 1GB |
| **AWS Lightsail** | Micro (IPv4) | 1 GB | 1 vCPU | 40 GB | $10/mo | Yes (VPS) | SSH | None (always on) | Comfortable fit |
| **Google Cloud Run** | Per-request | varies | varies | None local | ~$0-5/mo | **No** (ephemeral) | `gcloud deploy` | Yes (slow) | SQLite not viable without hacks |
| **Coolify on Hetzner** | CAX11 + Coolify | 4 GB | 2 vCPU | 40 GB | ~$4/mo | Yes (VPS) | `git push` (web UI) | None (always on) | Best of both worlds |

## Detailed Analysis

### Tier 1: Best Options

#### Hetzner Cloud (CAX11 or CX22) -- ~EUR 3.79/mo (~$4)

The clear winner on raw value. For under $4/month you get 4 GB RAM, 2 vCPUs, and 40 GB
NVMe storage. That is 4x the RAM you need, with plenty of room for CAMeL Tools models,
the SQLite database, and future growth.

**Pros:**
- Incredible price/performance ratio
- Full VPS with persistent filesystem (SQLite works natively)
- 4 GB RAM is very comfortable for CAMeL Tools
- 20 TB traffic included
- ARM (CAX11) or x86 (CX22) at the same price
- Always-on, no cold starts
- Hourly billing with monthly cap

**Cons:**
- EU-only datacenters (Germany/Finland) -- adds latency if user is elsewhere
- Bare VPS requires setup (SSH, systemd, nginx, etc.) unless using Coolify/Kamal
- No built-in git-push deploys (need to set up deployment yourself)

**Deployment options:**
1. **Coolify** (self-hosted PaaS on the same VPS) -- gives you a Heroku-like git-push experience for free
2. **Kamal** (from 37signals) -- deploy via `kamal deploy` from local machine
3. **Simple SSH + systemd** -- `rsync` code + restart service
4. **Docker + Watchtower** -- push to registry, auto-pull on server

#### Oracle Cloud Always Free -- $0/mo

Genuinely free forever: up to 4 ARM OCPUs and 24 GB RAM. If you can get an instance
provisioned, this is unbeatable.

**Pros:**
- Literally free, forever
- Massive resources (4 OCPU, 24 GB RAM, 200 GB storage)
- Full VPS with persistent filesystem
- Always-on

**Cons:**
- **Extremely difficult to provision** -- "Out of Host Capacity" errors are common in most regions
- Requires patience (scripts to retry provisioning for days/weeks)
- Oracle's cloud UI and documentation are notoriously confusing
- Risk of account termination if Oracle deems it "idle" (reports vary)
- Must convert to Pay-As-You-Go account (still free resources, but credit card on file)
- ARM architecture only for the free tier (CAMeL Tools should work on ARM but test first)

**Verdict:** Worth trying, but do not count on it as your primary plan.

### Tier 2: Good PaaS Options

#### Fly.io -- ~$7-8/mo

Fly.io is well-suited for SQLite workloads thanks to persistent volumes and LiteFS.
A shared-cpu-1x machine with 1 GB RAM runs about $5.92/mo, plus $0.15/GB/mo for a
persistent volume.

**Pros:**
- First-class SQLite support with persistent volumes
- `fly deploy` from CLI is simple and fast
- LiteFS available for SQLite replication (overkill for single-user but nice)
- Good Python/FastAPI documentation and templates
- Global edge network

**Cons:**
- No free tier (trial only)
- 1 GB RAM is the minimum comfortable config (~$6-8/mo total)
- **Warning:** Do not combine autostop/autostart with persistent SQLite (risk of data loss)
- Volumes are pinned to a single region/host
- Must keep machine running 24/7 (no scale-to-zero with SQLite)

**Deployment:** `fly deploy` from project directory. Detects Python automatically.

#### Railway -- ~$10-15/mo

Railway has the smoothest developer experience but is the most expensive option for
always-on workloads because RAM is billed at $10/GB/month.

**Pros:**
- Best DX: `railway up` or git-push deploys
- Persistent volumes for SQLite ($0.25/GB/mo)
- Auto-detects Python/FastAPI, no Dockerfile needed
- Nice dashboard and logs

**Cons:**
- $5/mo subscription fee regardless of usage
- RAM billed at $10/GB/month -- a 1 GB app running 24/7 costs ~$10 in RAM alone
- Total cost for this workload: $5 (sub) + ~$10 (1GB RAM) = ~$15/mo
- Exceeds budget significantly

### Tier 3: Workable but Compromised

#### Render Starter -- ~$9-10/mo

**Pros:**
- Git-push deploys, clean UI
- Persistent disk available on paid plans ($0.25/GB/mo)

**Cons:**
- Starter plan only has 512 MB RAM -- likely too tight for CAMeL Tools
- Standard plan ($25/mo) is well over budget
- Free tier has no persistent disk and spins down after inactivity
- Cold starts after spin-down would reload 300 MB of model data (slow)

**Verdict:** 512 MB is risky for this workload. Standard plan is too expensive.

#### DigitalOcean -- $6/mo (1 GB)

**Pros:**
- Simple, well-documented VPS
- 1 GB droplet at $6/mo with 25 GB SSD
- Per-second billing (2026)
- Extensive tutorials for Python deployment

**Cons:**
- More expensive than Hetzner for less resources
- Same VPS management burden without Coolify
- $4/mo droplet (512 MB) is too small for CAMeL Tools

#### AWS Lightsail -- $5-10/mo

**Pros:**
- Familiar AWS ecosystem
- Predictable flat pricing
- Free tier for first 3 months ($5 plan)

**Cons:**
- $5/mo plan has only 512 MB RAM (too tight)
- $10/mo plan (1 GB) is at the budget ceiling
- Less value than Hetzner (1 GB vs 4 GB for similar price)
- AWS console overhead

### Tier 4: Not Recommended

#### Google Cloud Run

Cloud Run's ephemeral containers are fundamentally incompatible with SQLite. Workarounds
exist (GCS FUSE, NFS via Filestore) but add complexity, latency, and cost. Filestore
alone starts at $200/mo. Cold starts with 300 MB model data would be painful.

**Verdict:** Wrong tool for this job.

## Recommendation

### Primary: Hetzner CAX11 + Coolify -- ~$4/mo

This is the clear winner. For EUR 3.79/month you get a full ARM VPS with 4 GB RAM,
2 vCPUs, and 40 GB storage. Install [Coolify](https://coolify.io) on it for free to
get a Heroku-like deployment experience with git-push deploys through a web UI.

**Setup (one-time, ~30 minutes):**

```bash
# 1. Create CAX11 instance on Hetzner Cloud dashboard
# 2. SSH in and install Coolify
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash

# 3. Open Coolify web UI (https://your-server-ip:8000)
# 4. Connect your GitHub repo
# 5. Configure environment variables
# 6. Deploy
```

After setup, deploying is just pushing to your configured branch.

**Why this wins:**
- 4 GB RAM is very comfortable (CAMeL Tools + SQLite + headroom)
- Persistent filesystem by default (it is a VPS)
- Coolify gives you git-push deploys, SSL, logs, and monitoring for free
- $4/mo is well under the $5/mo target
- Always-on, no cold starts, no spin-downs
- Full control over the server

### Fallback: Fly.io -- ~$7-8/mo

If you prefer a managed PaaS and want to avoid any server management, Fly.io is the
best PaaS option for SQLite workloads. The `fly deploy` command is simple, and persistent
volumes work well. Budget is slightly over the $5 target but within the $10 ceiling.

### Worth a shot: Oracle Cloud Free Tier -- $0/mo

Try to provision an Always Free ARM instance. If it works, you get far more resources
than you need for literally zero cost. But have Hetzner as your fallback because
provisioning can be unreliable.

## Quick Decision Matrix

| Priority | Choose |
|---|---|
| Cheapest possible | Oracle Cloud Free (if you can get it) |
| Best value with easy deploys | **Hetzner CAX11 + Coolify ($4/mo)** |
| Zero server management | Fly.io ($7-8/mo) |
| Absolute simplest DX | Railway ($15/mo, over budget) |
