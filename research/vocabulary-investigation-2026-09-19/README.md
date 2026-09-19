# Reproduce the vocabulary-attention investigation

Code analyzed: `c2fedbe2fbfd4974649fd48571b6795f29b7a453` (local and fresh remote main).
Production checkout observed: `f901a1b447351ed1a0e2cdc82a48e9291f32cb77`.
Production tracked worktree was clean; unrelated untracked files were not changed.
The services involved in rank scoring, QAC mapping, and source tiers were inspected at these revisions. The pending maintenance amendments are explicitly not treated as production results.

The online `.backup` snapshot was pulled on September 19, 2026, at approximately 13:17 UTC; all analysis uses a cutoff of `2026-09-19T13:17:00Z`. Its last ReviewLog timestamp is `2026-09-19 09:44:51.900065`. `PRAGMA quick_check` returned `ok`.

Database SHA-256: `8e685f9d3d282da6510f22a8f42671aff988565cbd63e5f5d1b591a75b61a305`.

The private DB and September JSONL/gz interaction logs are retained outside Git at `~/alif-backups/research-vocabulary-20260919/`. Raw novel text stays outside Git. The source data files are the existing `backend/data/MSA_freq_lists.tsv`, QAC morphology file and frozen Momo benchmark. Input checksums appear in `manifest.json` and `mapping-probe.json.gz`.

From the repository root, using the existing backend virtual environment:

```sh
backend/.venv/bin/python research/vocabulary-investigation-2026-09-19/analyze.py \
  --db ~/alif-backups/research-vocabulary-20260919/prod.db \
  --data-dir backend/data \
  --output-dir research/vocabulary-investigation-2026-09-19

backend/.venv/bin/python research/vocabulary-investigation-2026-09-19/probe_mapping.py \
  --db ~/alif-backups/research-vocabulary-20260919/prod.db \
  --text tmp/momo_full_2026-07-15.txt \
  --qac backend/data/frequency_sources/quranic-corpus-morphology-0.4.txt \
  --out research/vocabulary-investigation-2026-09-19/mapping-probe.json.gz

backend/.venv/bin/python backend/scripts/analyze_low_energy_maintenance_experiment.py \
  --db ~/alif-backups/research-vocabulary-20260919/prod.db \
  --interaction-log-dir ~/alif-backups/research-vocabulary-20260919/logs \
  --start 2026-09-03T10:00:00Z --cutoff 2026-09-19T13:17:00Z \
  --output research/vocabulary-investigation-2026-09-19/maintenance.json
```

In the isolated research worktree, the existing parent checkout's interpreter and data paths were passed as absolute paths; no dependency install or app startup was needed. SQLite connections use `mode=ro&immutable=1` plus `query_only`; the ORM probe uses its own read-only connection factory, not the app's writable engine. Frequency repair uses detached objects only. Snapshot hashes are checked afterward.

Interpretation constraints:

- Frequency bins use current mappings/ranks applied retrospectively. Missing ranks are reported separately and repaired only in memory. Old non-NULL ranks are preserved, as in the proposed backfill.
- “Rank >5,000” is a weak composite/surface proxy, not a semantic judgment of uselessness. Current per-sentence mappings and targets can differ from historical presentation. ReviewLog primary/collateral fields are the stronger attribution evidence.
- June–August SentenceReviewLog includes passage children. September maintenance has no automatic passages; month-to-month rows are not equivalent cards/minutes.
- Recorded response latency is not active effort. `capped_response_ms` is only a diagnostic sum of positive records ≤300 seconds, not estimated recoverable time. The report does not allocate whole sentence duration to a particular word.
- The old maintenance checker uses a slightly different function-word classification and does not prove exposure-free delayed retention. Its 1,636 scheduled-judgment denominator differs from this audit's 1,634. The audit's canonical due partition is not a replacement for the checker's strict debt definition.
- Acquisition ordering is evaluated with the current candidate function and snapshot. Its date-sensitive bonuses use execution time; same-day reproduction is the relevant scope. It is not a historical session replay and does not override downstream admission gates.
- Source/cohort labels describe current provenance, not randomized treatment. Historical leech reintroductions can overwrite earlier provenance.
- Full Momo mapping is token-local via the existing shared resolver. Unresolved and ambiguous occurrences stay visible; neither marked-unknown nor apparently unambiguous automatically means correctly identified. No full novel text or contextual QA is claimed.
- The QAC probe reruns current mapping against the snapshot; it is not a provenance trace for every historical core row. The wine-jug source count and rerun agree exactly. Coarse POS mismatches are candidates for review, not a verified error count.
- The frozen July Momo map is stale and has different tokenization. Its OOV bare strings cannot recover lost token context safely.
- No application code or production learner data changed, and no providers were called by the analysis. HTTP source inspection is separate from the database/mapping probes.
