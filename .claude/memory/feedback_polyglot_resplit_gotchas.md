---
name: polyglot-resplit-gotchas
description: "Two gotchas when bulk-replacing polyglot Pages (e.g. split_long_pages.py) — Sentence FK nulling must include inactive rows, and polyglot's log_activity signature differs from alif's."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 40ff402b-509a-44d2-ae81-b0657fe14db3
---

When writing a polyglot script that replaces `Page` rows in place (re-split, re-paginate, re-import), two things will bite:

1. **`PRAGMA foreign_keys=ON` is enforced** (`polyglot/app/database.py:20`). `sentences.page_id` has no `ondelete=` cascade. Before deleting a `Page` you must null the FK on **every** Sentence pointing at it — including `is_active=False` rows. Filtering "active only" passes dry-run but fails on apply because old harvest corrections leave inactive Sentence rows behind. Caught 2026-05-27 on prod story 1, page 12.

2. **`log_activity` signatures differ between projects**:
   - Alif: `log_activity(event_type, summary, *, detail=...)` (positional event_type, summary)
   - Polyglot: `log_activity(db, *, event_type, summary, detail=..., language_code=...)` (takes a session, all keyword-only)
   See `polyglot/app/services/activity_log.py:17`. Copy-pasting from an alif script will TypeError at runtime.

**Why:** Both burned a real production run of `scripts/split_long_pages.py` on 2026-05-27. The FK bug crashed mid-apply; the log_activity bug crashed *after* commits had landed (no data damage but no audit trail).

**How to apply:** When porting any Alif script that does Page replacement or activity logging into polyglot, grep these two functions before running. For cascade-FK awareness in general: `grep "ForeignKey(\"pages.id\")" polyglot/app/models.py` (only `sentences` and `page_words` today; `page_words` cascades via SQLAlchemy relationship).

Related: [[feedback_lemma_deletion_fks]] (the alif analogue for `lemmas.lemma_id` inbound FKs).
