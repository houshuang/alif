---
name: Spanish Pilot (spanish-pilot/)
description: Norwegian-school Spanish pilot testing Alif's word-level UX before committing to full port. Lives in alif/spanish-pilot/, deployed separately on Hetzner.
type: project
originSessionId: 1d660f93-be34-4ece-a765-c0e8239240ef
---
Stian met a Norwegian school (60 students) wanting to pilot an Alif-style Spanish trainer. Instead of adapting the Alif codebase (which is deeply Arabic-specific — single-user, CAMeL Tools, triliteral roots, RTL), we're building a small standalone `spanish-pilot/` prototype to UX-test the core Alif experience (word-level SRS + lemma word-detail cards with memory hooks + etymology) with Norwegian translations (NOT English — no English allowed anywhere in the app).

**Why:** Validate that the flip-and-grade + word-detail-card UX actually works for classroom students before investing in the multi-user/Spanish rewrite. If UX flops, we don't need to do the big port.

**How to apply:**
- Everything lives in `/Users/stian/src/alif/spanish-pilot/` — separate subfolder, separate SQLite, separate systemd unit on Hetzner (port 3100, `alif-spanish-pilot.service`).
- Core scheduling: word-level FSRS-6 + Leitner 3-box acquisition (matches Alif's three-phase lifecycle), sentence-first review surface.
- Content must be perfect — pre-generated offline, two-pass Claude verification, human-approved before deploy. Bad Spanish or unnatural Norwegian kills the pilot.
- Testing comparing self-grade vs multiple-choice UX — both modes built, per-student toggle.
- Login: name-only, clickable list of registered students.
- Norwegian UI, no English.
- UX features being tested (core value prop): memory hooks, etymology, conjugation tables in word-detail cards, Leitner box visualization in dashboard, total known words count.
