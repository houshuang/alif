---
name: feedback_polyglot_local_db_stale
description: Local polyglot.db (and alif.db) are stale — real review data lives on the prod server; always query prod for analysis
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e2c67502-7394-4fd4-bcf7-0c9a1538afd2
---

The local `polyglot/polyglot.db` (and `backend/data/alif.db`) are **stale dev copies**. All real user reviews/usage happen against the **production** server (Hetzner `alif`). The local DBs may show 0 reviews / seed-only data even when the user has done lots of real reviews.

**Why:** Polyglot runs as `polyglot-backend` systemd on the prod server; the user does reviews there, not locally. On 2026-05-29 an Explore agent reported "Polyglot: 0 reviews logged" from the local DB and I started building simulations on assumed parameters — the user stopped me: "something is very broken... I have done lots of polyglot reviews."

**How to apply:** Before ANY data analysis, forgetting-curve fitting, FSRS optimization, review counts, or "current state" claims about Alif or Polyglot, **fetch fresh data from prod** — `ssh alif` to the production DB (`/opt/alif/...`), or pull a backup. Never quote counts/state from the local `.db` files. When the user wants to "gather data and not assume," the real data already exists on prod — go get it. Related: [[feedback_verify_before_recommending]], [[feedback_db_queries]].
