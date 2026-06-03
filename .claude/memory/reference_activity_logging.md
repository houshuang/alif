---
name: reference_activity_logging
description: How batch scripts + manual Claude actions log to ActivityLog (visible in app More → Activity)
metadata:
  type: reference
---

All batch scripts log to ActivityLog via `app.services.activity_log.log_activity()`. Logs are visible in the app's More tab → Activity section.

**Manual logging from Claude** (always do this after a manual backfill/fix from the CLI):
```
ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 scripts/log_activity.py EVENT_TYPE 'Summary text' --detail '{\"key\": \"val\"}'"
```

**Event types**: `manual_action`, `material_updated`, `sentences_generated`, `audio_generated`, `sentences_retired`, `frequency_backfill_completed`, `grammar_backfill_completed`, `examples_backfill_completed`, `variant_cleanup_completed`, `flag_resolved`.

Note: Alif's `log_activity` signature differs from Polyglot's — see [[feedback_polyglot_resplit_gotchas]]. Backups before manual changes: [[reference_backups]].
