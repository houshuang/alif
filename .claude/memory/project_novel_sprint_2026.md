---
name: project_novel_sprint_2026
description: "Active goal 2026-10-04 → 2026-12-31 — finish Kanafani's Men in the Sun with a weekly L1 oral-recall + speed test; reading_targets drive intake; local main diverged from origin"
metadata:
  node_type: memory
  type: project
  originSessionId: b009e455-5c97-43a9-a334-99af9e43fadc
  modified: 2026-10-04T11:44:15.811Z
---

**Active goal (set 2026-10-04):** read *رجال في الشمس* (Men in the Sun, 7 chapters, 14,126
running words) cover to cover by 31 December 2026; stretch: finish *Momo*. Weekly Sunday
test = timed unvocalized fresh passage + oral free recall in English/Norwegian scored
against idea units (F vs prompted, distortions, intrusions) + 2–3 probes. The user chose
this after rejecting a 9-month framing ("a real goal I can crush in 3 months", willing to
invest substantially more time than Aug–Sep).

Protocol, schedule and weekly log: `research/novel-sprint-2026-10/` (README, weekly.md,
sunday_test.py, recall-protocol-research.md, petrarca-voice-audit.md). Decision record:
experiment-log `2026-10-04 "Novel sprint v1"`. Policy `novel_sprint_v1` is live on prod
(`ALIF_NOVEL_SPRINT=1` in `/opt/alif/.env`): cap 8/day, ladder 20/60 cards, 0/4/8;
`reading_targets` table + `scripts/novel_sprint_feed.py` (needs a reviewed gloss JSON for
OOV words because `/api/discover/words` glossing is broken on prod).

Book text (not in repo): `~/src/bookifier/bilingual/input/rijal_full/0N_*.txt` (Gemini OCR of
the Rimal 2013 edition from archive.org item 20220115_20220115_1244; ch1 equals the reviewed
gold text), `glosses_chN.json`, and the Sunday passages under `.../rijal_full/sunday/`.

**Why:** the plateau came from endless review with no finish line; a book with the best
coverage curve measured (ch1 86.9% → 95% after 100 words) plus a weekly fail-able test is
the motivational bet. **How to apply:** before any scheduler/intake change until 2027, read
the protocol and keep the maintenance card rules; feed targets one chapter ahead; keep the
Sunday rows honest (prepared ≠ read). Related: [[feedback_progress_metrics_verified_not_activity]].

**Checkout hazard (2026-10-04):** local `~/src/alif` main has 4 unpushed commits
(f902966…335524b, Sep-23 model refresh to gpt-6-sol) that diverge from origin/main
(#283 moved to gpt-6.1-sol). `git pull --ff-only` fails; branch new work from `origin/main`
(`git checkout --no-track -b sh/x origin/main`) and deploy via the server's own pull. Do not
reset local main without the user.
