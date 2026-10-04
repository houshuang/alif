# Petrarca voice-based assessment audit

Petrarca's voice-based assessment is a working end-to-end pipeline. It records on an iPhone, transcribes with Soniox and scores with Claude Sonnet or Opus prompts. Alif already has the Soniox service and a one-minute voice recorder in the chapter reader, but the recorder does not transcribe or score anything.

## Petrarca (`/Users/stian/src/petrarca`)

### Speech-to-text
- `/Users/stian/src/petrarca/scripts/research-server.py:1395`: `transcribe_on_server()` uploads to Soniox (`https://api.soniox.com/v1`), creates a transcription and polls every 2 s for up to 3 min (90 polls). It then joins the returned tokens and deletes the transcription and file. Settings: `model='stt-async-v4'`, upload sent as `note.m4a` / `audio/m4a`, and these language hints:
  ```
  ['en','no','sv','da','it','de','es','fr','zh','id']
  ```
- `/Users/stian/src/petrarca/research/voice-processing.md` covers model choice (`stt-async-v4` batch, `stt-rt-v4` streaming), pricing at about $0.10/hour, code-switching, per-token language ID and the temporary-key option (`/v1/auth/create_temporary_api_key`). (Credential hygiene note handed to the owner separately.)
- `/Users/stian/src/petrarca/CLAUDE.md` § Voice Processing points to Alif's `backend/app/services/soniox_service.py` as the reference pattern.

### Audio capture on the client (Expo, `expo-av ~16.0.8`)
- `/Users/stian/src/petrarca/app/components/MicButton.tsx` records with `Audio.RecordingOptionsPresets.HIGH_QUALITY` (m4a) after `setAudioModeAsync({allowsRecordingIOS, playsInSilentModeIOS})`, and shows a timer. On web it shows "Recording unavailable on web".
- `/Users/stian/src/petrarca/app/lib/audio-upload-queue.ts` is a crash-safe upload queue. It saves the audio to a persistent dir before uploading and tracks pending items in JSON on disk. Each upload gets an idempotent request ID. Uploads have a 90 s timeout, retry on mount and when the app returns to the foreground, and expire after 48 h. A listener pattern updates the UI on background retry success. Test: `/Users/stian/src/petrarca/app/__tests__/audio-upload-queue.test.ts`. This is the most reusable client piece.
- `/Users/stian/src/petrarca/app/app/voice-elicitation.tsx` is the guided-recall UI. It handles node, chapter (`chapter:{book_id}:{n}`), book (`book:{id}`) and era-sweep (`sweep:{era_id}`) candidates, a "Know Nothing" button and Skip, and auto-loads more candidates. Recordings are saved to a pending dir as `elicit_{ts}.m4a` before upload.
- `/Users/stian/src/petrarca/app/app/knowledge-sweep.tsx` runs a full-domain sweep. The flow is domain_select, ready, recording (one recording per era), era_done, scoring, results.
- `/Users/stian/src/petrarca/app/app/defender.tsx`, `/Users/stian/src/petrarca/app/components/VoiceFeedback.tsx` and `/Users/stian/src/petrarca/app/components/ExplorerCapture.tsx` are other audio-input surfaces.
- `/Users/stian/src/petrarca/app/lib/review-api.ts` holds the client API types for elicitation and sweeps.

### Server endpoints (all in `/Users/stian/src/petrarca/scripts/research-server.py`)
- Recall endpoints: `/review/voice-elicit` (handler `_handle_voice_elicitation` at :6145), `/review/voice-elicit-check` (:6325), `/review/voice-memo` (:6112) and `/book/voice-note` (:2990). Also `_handle_elicit_candidates` (:6457) and `_handle_elicit_know_nothing` (:6473).
- Results are cached by request ID in `VOICE_ELICIT_CACHE_DIR` (`/opt/petrarca/data/voice_elicit_cache`).
- Sweep endpoints: `GET /knowledge/sweep/domains`, `GET /knowledge/sweep/plan/{id}`, `POST /knowledge/sweep/submit`, `GET /knowledge/sweep/gaps`, `POST /knowledge/sweep/transcribe`, `GET /knowledge/sweep/history/{id}`.
- `/voice/calibration` (:7665) is an HTML page for checking captures, backed by `/Users/stian/src/petrarca/scripts/voice_calibration.html`. Related analysis pages: `/Users/stian/src/petrarca/scripts/voice-capture-analysis.html`, `/Users/stian/src/petrarca/scripts/knowledge_elicitation_analysis.html`.

### Scoring (`/Users/stian/src/petrarca/scripts/review_engine.py`)
- `run_voice_elicitation()` at :4703 transcribes, builds the prompt from the node definition plus book source texts, and calls `call_claude_json` (in `/Users/stian/src/petrarca/scripts/claude_llm.py`, default model `sonnet`, 180 s timeout).
  - It deliberately avoids holding the SQLite write lock during transcription and LLM calls (`conn` can be None; it manages its own connections).
  - Era-sweep pseudo-nodes are routed to `run_era_sweep()`; transcripts under 5 words return `too_short` ("Try speaking for at least 30 seconds").
  - The model's score maps to FSRS ratings through `SCORE_TO_FSRS` at :73:
    ```
    knew   -> Easy    (~8.3d initial stability, ~28d first due)
    partly -> Good    (~2.3d initial stability, ~8d first due)
    missed -> Again   (~0.2d initial stability, ~1d first due)
    ```
  - The same score sets the knowledge level: knew → anchored, partly → engaged, missed → mentioned. New knowledge_items get 14-day initial stability. Every transcript is logged to `voice_transcripts` via `_log_voice_transcript`.
  - `confidence_tagged` "wrong" facts create `correction` microlearning cards. Missed facts deliberately do NOT create cards (user prefers reading to fill gaps), except one ML card for the biggest gap.

- `VOICE_ELICITATION_PROMPT` at :450 scores per-topic free recall. Full text:
  ```
  Analyze a learner's free recall about a historical topic.

  TOPIC: {node_title}
  TOPIC DEFINITION: {node_description}

  BOOK SOURCES (what the learner has read about this):
  {sources_text}

  AVAILABLE NODES IN THIS CURRICULUM:
  {available_nodes}

  LEARNER'S RECALL (transcribed speech):
  {transcript}

  Compare the learner's recall against the topic definition and book sources. Identify:

  1. CAPTURED: Specific facts or concepts from the definition/sources that the learner mentioned (even if imprecisely). Be generous — paraphrases count.
  2. MISSED: The 2-3 most structurally important omissions — facts that serve as scaffolding for understanding the broader topic (key dates, actors, causal relationships). Prefer load-bearing facts over colorful details.
  3. INTERESTING: Things the learner said that go BEYOND the sources — personal connections, questions, hypotheses, links to other topics. These are valuable signals.
  4. WONDERINGS: Extract ALL questioning or curious statements — "I wonder...", "I'm not sure if...", "was it...?", "I'd like to know...", hedged questions, speculative connections, anything where the learner is reaching beyond what they know. These are the most valuable signals — err on the side of including too many. Rephrase as clear research questions.
  5. RESEARCH_QUESTIONS: Specific questions that could be researched to deepen the learner's understanding. Derive from wonderings, gaps in knowledge, and interesting but uncertain claims. Frame as searchable questions.
  6. ENTITIES_MENTIONED: List ALL people, places, events, and concepts the learner mentions by name. Use canonical forms (e.g., "Alexander the Great" not "Alexander").
  7. CONFIDENCE_TAGGED: For each key claim the learner makes, tag their apparent confidence: "certain" (stated as fact), "uncertain" (hedged, "I think...", "maybe..."), or "wrong" (stated confidently but incorrect).
  8. ORGANIZING_FRAMEWORK: How does the learner organize this knowledge? Options: "biographical_arc" (follows a person's life), "chronological" (events in order), "geographic" (places and regions), "thematic" (ideas and concepts), "causal" (cause and effect chains).
  9. ADJACENT_NODES_COVERED: If the learner discusses topics that clearly overlap with OTHER curriculum nodes (not this one), list the likely node_ids from the available nodes above.

  If the learner demonstrates extensive knowledge about adjacent or broader topics beyond the node definition, acknowledge this in feedback_summary and give partial credit in coverage_pct for related knowledge that connects to this topic.

  Output JSON:
  {"captured": [...], "missed": [...], "interesting": [...], "wonderings": [...], "research_questions": [...], "entities_mentioned": [...], "confidence_tagged": [{"fact": "...", "confidence": "certain"}], "organizing_framework": "biographical_arc|chronological|geographic|thematic|causal", "adjacent_nodes_covered": [...], "coverage_pct": 65, "suggested_score": "knew|partly|missed", "feedback_summary": "2-3 sentence personalized feedback highlighting what was strong and what key thing was missed"}
  ```

- `SWEEP_SCORING_PROMPT` at :7360 (full-domain) and `ERA_SWEEP_SCORING_PROMPT` at :7431 (one era) are the closest model for a weekly oral test. Full text of the domain version:
  ```
  Score a knowledge sweep — a learner's free recall across an entire curriculum domain.

  DOMAIN: {domain_title}

  The learner was asked to recall what they know about each era/topic in this domain.
  Below is their transcript (may cover multiple eras recorded sequentially).

  TRANSCRIPT:
  {transcript}

  CURRICULUM NODES (Level 1 and 2 — the expected coverage for a sweep):
  {nodes_with_facts}

  For EACH Level 1/2 node in the curriculum:
  1. Was it mentioned at all? (yes/no)
  2. What specific facts were stated about it? For each fact:
     - The claim (brief)
     - Correct or incorrect?
     - Match to a key_fact ID if possible
     - Excerpt from transcript
  3. What depth was demonstrated?
     - "surface": just named or referenced in passing
     - "textbase": recalled specific facts (dates, names, events)
     - "situation_model": showed causal reasoning, connections to other nodes, perspective-taking

  For CONNECTIONS between nodes:
  - List every pair of nodes the learner explicitly connected
  - Type: causal ("X led to Y"), temporal ("at the same time as"), comparative ("unlike X, Y..."), cross_domain

  For ORGANIZATION:
  - Did the learner proceed chronologically? Thematically? Randomly?
  - Count causal language ("because", "led to", "as a result", "which caused")
  - Count perspective-taking and counterfactual statements

  IMPORTANT scoring rules:
  - Be generous with matching: paraphrases count, approximate dates count (±20 years for ancient, ±5 for modern)
  - A vague reference ("the Greeks colonized Sicily") counts as surface mention of the relevant node
  - Only mark "incorrect" for clear factual errors (wrong century, wrong attribution, events that didn't happen)
  - If the learner conflates two nodes, credit both as mentioned

  Output JSON:
  {
    "nodes": [{"node_id": "exact_id", "node_title": "...", "mentioned": true, "depth": "surface|textbase|situation_model",
               "facts": [{"claim": "...", "correct": true, "key_fact_id": "id_or_null", "excerpt": "..."}]}],
    "connections": [{"from_node": "node_id", "to_node": "node_id", "type": "causal|temporal|comparative|cross_domain", "excerpt": "..."}],
    "organization": {"pattern": "chronological|thematic|random|mixed", "causal_count": 5, "perspective_count": 1, "counterfactual_count": 0},
    "errors": [{"claim": "incorrect statement", "correction": "what actually happened", "node_id": "relevant_node"}],
    "strongest_area": "...",
    "biggest_gap": "...",
    "summary": "2-3 sentence assessment of the learner's overall knowledge structure"
  }
  ```
  The era version uses the same per-node structure (mentioned, facts with correct/key_fact_id/excerpt, depth), connections and errors, with the shorter rule "be generous — paraphrases count, approximate dates count. Only mark 'incorrect' for clear errors." It adds `coverage_pct`, `suggested_score` (knew|partly|missed), `summary`, `strongest_node` and `biggest_gap`, so it slots into the elicitation UI.
  - The depth scale follows Kintsch's surface / textbase / situation-model levels of comprehension.
  - `run_era_sweep()` writes results to the `knowledge_sweeps` table (domain_id, era, transcript, scores JSON, node-level results). Wrong facts become `sweep_correction` microlearning cards; fuzzy chronology produces `sweep_timeline` cards; per-node depth updates knowledge levels.
  - Session 63 notes the full-domain sweep was scored with Opus.

- `VOICE_EXTRACT_PROMPT` at :435 is a lighter memo scorer: remembered facts, questions, connections, confidence → `{"remembered","questions","connections","suggested_score"}`.
- `VOICE_CAPTURE_ANALYSIS_PROMPT` at :482 and `VOICE_CAPTURE_ENTITY_PROMPT` at :520 handle captures that are explicitly "NOT a recall test". They extract every fact, map facts to nodes only if genuinely about the same subject and period ("When in doubt, leave the fact unmapped"), and grade each node as anchored / engaged / mentioned. Per-fact confidence has worked good/bad examples: "uncertain" requires the learner's own hedging language, not topic difficulty; "wrong" requires the model to know the learner is mistaken, and "When in doubt between certain and wrong, prefer certain".
- `DOMAIN_SUMMARY_PROMPT` at :4494 synthesizes a learner knowledge portrait from voice transcripts.
- `/Users/stian/src/petrarca/scripts/defender_engine.py` holds `DEFENDER_OBJECTION_PROMPT` (:195) and `DEFENDER_GRADE_PROMPT` (:221). These grade a spoken defence of a thesis (engagement + evidence: specific/general) and never touch FSRS; they never downgrade knowledge ("deflection in debate ≠ ignorance").

### Research and lessons
- `/Users/stian/src/petrarca/research/session-changelog.md:1230` (Session 63, 2026-04-09) records the first sweep findings (Sicily, 2 of 7 eras).
  - Spontaneous recall was 32.5% (13/40 nodes) against 100% system coverage from reading: "reading ≠ retrieval".
  - Facts that were stated were 95.3% accurate; 4 factual errors corrected; 8 causal/temporal connections.
  - Greek Sicily showed situation-model depth; Arab-Norman Sicily showed scattered facts without causal linking.
  - Full-domain sweeps were too exhausting at 5+ minutes per era, so they were replaced by era-level sweeps mixed into the recall queue (`_era_sweep_candidates()`, eras not swept in 14+ days, SWEEP badge).
  - Design point: sweeps measure spontaneous retrieval organization, different from quiz-prompted recall; the gap between them is the diagnostic.
- `/Users/stian/src/petrarca/research/knowledge-elicitation-deep-analysis.md` (2026-04-06) finds transcripts were write-only. Only `suggested_score`, `wonderings` and `research_questions` fed anything downstream; `interesting`, `captured` and `feedback_summary` were never reused. It distinguishes fresh recall (chapter just read: retention prevention, `missed` matters) from distant recall (personal salience mapping, `interesting` matters).
- `/Users/stian/src/petrarca/research/knowledge-elicitation-iteration-notes.md` (2026-03-16) covers the testing effect, graduated depth probes, latent versus active knowledge, and guided (prompted) voice dumps instead of raw dumps.
- `/Users/stian/src/petrarca/research/knowledge-assessment-research.md` §2 makes the case for free recall as assessment (Tulving ecphory, semantic network estimation from free recall).
- `/Users/stian/src/petrarca/research/historical-thinking-assessment-frameworks.md` surveys rubrics such as Seixas' Big Six and AP exam rubrics.
- `/Users/stian/src/petrarca/research/voice-microlearning-integration.md` and `/Users/stian/src/petrarca/research/knowledge-growth-measurement-proposal.md` cover downstream use and measurement.
- `/Users/stian/src/petrarca/research/srs-humanities/04-tibetan-debate.md` is the background to defender mode; `/Users/stian/src/petrarca/research/srs-humanities/08-locke-commonplace.md` to commonplace resurfacing.
- `/Users/stian/src/petrarca/CLAUDE.md` § Production Data Discipline sets the gotchas:
  - Never POST synthetic text to recall or capture endpoints on prod. Real captures were lost this way in Session 86 (2026-04-20) when dedup jobs removed rows indistinguishable from test rows.
  - Stamp `voice_transcripts.input_mode` (`audio`, `text_json` or `test`) at ingest so real recordings can be told apart from test rows.
  - Test via `scripts/pipeline-tests/run.py` fixtures or in-memory SQLite.
  - Transcript dedup uses a SHA-256 hash of the transcript text.
  - Session 88/89: Gemini was removed after a 429 silently dropped a generated question; Claude CLI via Max is the default LLM.

## Alif today (`/Users/stian/src/alif`)
- `/Users/stian/src/alif/backend/app/services/soniox_service.py` is a full Soniox wrapper using `stt-async-v4`, with upload, poll (3 s interval, 10 min timeout), fetch and cleanup, language hints and diarization. It is configured through `settings.soniox_api_key`. Its only current caller is `/Users/stian/src/alif/backend/scripts/import_michel_thomas.py`.
- `/Users/stian/src/alif/frontend/components/reading-voice-note.tsx` is the chapter-reader reflection recorder. It records up to one minute in `expo-av`, lets the user listen, discard or re-record, and keeps a base64 draft capped at 1.5 MB decoded; a submitted note moves into the durable outbox before its draft clears.
- `/Users/stian/src/alif/backend/app/routers/books.py:40` handles `POST /api/books/chapters/voice` (`{event, mime_type, audio_base64, duration_ms}`; WebM, MP4/M4A, Ogg). Line 48 handles `GET /api/books/chapters/voice/{event_id}`. The audio is stored content-addressed in `backend/data/reading-voice/`; the DB stores filename, MIME type and duration in `reading_pilot_events`.
- `/Users/stian/src/alif/docs/supported-reading-chapters.md` lines 28-33 and 59-79 state that there is no transcription, no deletion and no retention expiry, and that chapter reading is scheduler-inert.
- `/Users/stian/src/alif/research/petrarca-integration-plan.html` is an older integration plan that does not cover voice.

## What carries over to a weekly oral-recall test in Alif
1. **Speech-to-text:** call `SonioxService` from the existing chapter-voice upload path, in a background step after the blob is committed. For Arabic plus English, the hints should be `['ar','en']`.
2. **Recording:** reuse `reading-voice-note.tsx`, which already uses the same `expo-av` API as Petrarca's MicButton. If one minute is too short for a weekly test, raise the cap (Petrarca's sweep lesson: 5+ minutes per section is too exhausting; keep each prompt short). Port Petrarca's upload queue only if the existing sync-queue outbox is not enough.
3. **Scoring:** adapt `ERA_SWEEP_SCORING_PROMPT`. The expected nodes would be the week's passages or chapters, with key facts or idea units. Keep its per-claim correctness, its excerpts, the surface/textbase/situation_model depth, the connections, the errors list and the generous-matching rules. Borrow `VOICE_CAPTURE_ANALYSIS_PROMPT`'s confidence-tagging rules (hedge language, prefer certain over wrong).
4. **Evidence boundary:** keep the result away from the scheduler. Petrarca maps the score straight onto FSRS, but Alif's chapter reading is explicitly inert for scheduling. A scored oral test should go into a separate table, like Petrarca's `knowledge_sweeps`, unless an experiment-log entry first decides otherwise.
5. **Data discipline:** stamp each row with an `input_mode`, store the transcript next to the audio (Petrarca's lesson: transcripts were write-only, so make them queryable), and run speech-to-text and the LLM call outside any SQLite write transaction. Use `json_schema=` for the Claude CLI scoring call.
