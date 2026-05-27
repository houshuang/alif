# Data Model

SQLAlchemy models in `backend/app/models.py`. Pydantic schemas in `backend/app/schemas.py`.

## Core Tables
- `roots` — 3/4 consonant roots: core_meaning, productivity_score, enrichment_json (LLM-generated: etymology_story, cultural_significance, literary_examples, fun_facts, related_roots)
- `pattern_info` — Morphological pattern metadata: wazn (PK, e.g. "fa'il"), wazn_meaning, enrichment_json (LLM-generated: explanation, how_to_recognize, semantic_fields, example_derivations, register_notes, fun_facts, related_patterns)
- `lemmas` — Dictionary forms: root FK, pos, gloss, frequency_rank, cefr_level, grammar_features_json, forms_json, example_ar/en, transliteration, audio_url, canonical_lemma_id (variant FK), source_story_id, word_category (NULL=standard, proper_name, onomatopoeia), thematic_domain, etymology_json, memory_hooks_json, wazn (morphological pattern e.g. "fa'il", "maf'ul", "form_2", indexed), wazn_meaning (human-readable pattern description), forms_translit_json (ALA-LC transliteration per forms_json key, e.g. {"present": "yaktub", "plural": "kutub"}), gates_completed_at (timestamp set by `run_quality_gates()` — NULL means ungated, session builder rejects), decomposition_note (nullable JSON audit metadata from lemma-decomposition audit: `{mle_misanalysis: bool, reason, source_artifact, tagged_at, phase}` — stamped by Step 4b+ on orphan compounds whose CAMeL MLE decomposition proved wrong; query: `json_extract(decomposition_note, '$.mle_misanalysis') = 1`)
- `frequency_core_entries` — Weighted high-frequency curriculum ranks. `core_rank` is a continuous teachable-content rank; `lemma_id` links to an Alif lemma when mapped and stays NULL for honest missing-from-DB gaps. Stores source evidence (`camel_rank/count`, `buckwalter_rank`, `artenten_rank`, `kelly_rank/cefr`, `hindawi_rank`, `news_rank`, `islamic_rank`, `broad_source_count`, `confidence_tier`, `gap_status`, `source_flags_json`) plus display/gloss fields for stats.
- `user_lemma_knowledge` — Per-lemma SRS state: knowledge_state (encountered/acquiring/new/learning/known/lapsed/suspended), fsrs_card_json, times_seen, times_correct, times_heard (passive listening count, incremented by mark-story-heard), total_encounters, source (study/duolingo/textbook_scan/book/story_import/frequency_core/auto_intro/collateral/leech_reintro — preserved through acquisition, not overwritten), variant_stats_json (diagnostic per-surface seen/missed/confused counts; may include `form_key`/`form_label` when the surface matches `forms_json`; never an independent scheduling unit), acquisition_box (1/2/3), acquisition_next_due, entered_acquiring_at (when word entered Leitner pipeline), graduated_at, leech_suspended_at, leech_count, experiment_group (nullable, `intro_ab_card` for standard card-first acquisition; legacy `textbook_preserve_intro` rows may exist but no longer generate cards), experiment_intro_shown_at (nullable, timestamp when intro card was shown — prevents re-showing)

## Sentences & Reviews
- `sentences` — Generated/imported: arabic_text (fully diacritized — all pipelines store the voweled form; callers needing plain text strip diacritics at query time), english_translation, transliteration, target_lemma_id, story_id (FK to stories, for book-extracted sentences), source (llm/book/corpus/michel_thomas/tatoeba/manual), times_shown, last_reading_shown_at/last_listening_shown_at, last_reading_comprehension/last_listening_comprehension, is_active, max_word_count, created_at, page_number (for book sentences), mappings_verified_at (nullable DateTime — NULL=never verified, timestamp=when last verified by batch LLM check)
- `sentence_words` — Word breakdown: position, surface_form, lemma_id, is_target_word, grammar_role_json. Proper names should point at a `lemmas.word_category="proper_name"` row rather than a standard content lemma; that keeps them clickable while excluding them from scheduling/review credit.
- `review_log` — Review history: rating 1-4, mode, sentence_id, credit_type (metadata only), is_acquisition, was_confused (bool, explicit confusion signal), fsrs_log_json (pre-review snapshots for undo).
- `sentence_review_log` — Per-sentence review: comprehension, timing, session_id
- `confusion_captures` — User-reported word confusion ground truth (added 2026-05-27). When user marks a word "did not recognize" (yellow), an optional picker appears with algorithmic candidates + a free-text input. Each row: failed_lemma_id, capture_method ('suggested_pick'|'free_text'), confused_with_lemma_id (when picked) OR confused_with_text (when typed), candidates_shown_json (which suggestions were offered — so we can later answer "did the algorithm ever guess right?"), rating (1=Again, 2=Hard), and unresolved `resolved_lemma_id`/`resolution_method` columns filled later by Claude-driven analysis batches. Schema designed for accumulating ground-truth without active intervention; first analysis pass will happen after ≥50 captures.

## Grammar
- `grammar_features` — 24 features across 5 categories
- `sentence_grammar_features` — Sentence ↔ grammar junction
- `user_grammar_exposure` — Per-feature: times_seen, times_correct, comfort_score

## Stories & Content
- `stories` — title_ar/en, body_ar/en, transliteration, source (generated/imported/book_ocr), status (active/completed/suspended), readiness_pct, difficulty_level, page_count (for book imports), format_type (standard/long/breakdown/arabic_explanation), archived_at (DateTime nullable — orthogonal to status), audio_filename (MP3 in data/story-audio/), voice_id (ElevenLabs voice used), metadata_json (format-specific data e.g. explanation_ar). API returns page_readiness array (per-page new_words/learned_words/unlocked — counts only words unknown at import time, using acquisition_started_at vs story.created_at), new_total/new_learning (deduplicated story-level counts), and sentences_seen for book_ocr stories.
- `story_words` — Per-token: position, surface_form, lemma_id, gloss_en, is_function_word, name_type. Proper names use `name_type` (`personal`/`place`) plus a proper-name lemma row, count as known/inert for readiness, and should not create tracked `user_lemma_knowledge`.
- `page_uploads` — OCR tracking: batch_id, status, extracted_words_json, new_words, existing_words, textbook_page_number (detected printed page number from OCR)

## Quran
- `quranic_verses` — surah, ayah, surah_name_ar/en, arabic_text (Uthmani tashkeel), english_translation (Sahih International), transliteration. SRS state: next_due, srs_level (0=unseen, 1-7=learning, 8=graduated), last_rating, times_reviewed. lemmatized_at tracks lazy lemmatization. Unique on (surah, ayah). 6236 rows from risan/quran-json CDN.
- `quranic_verse_words` — Per-token: verse_id (FK), position, surface_form, lemma_id (FK nullable), is_function_word. Created by `lemmatize_quran_verses()`.

## System
- `content_flags` — Flagged content: content_type, status (pending/reviewing/fixed/dismissed)
- `activity_log` — System events: event_type, summary, detail_json
- `variant_decisions` — LLM variant cache: word_bare, base_bare, is_variant, reason
- `chat_messages` — AI conversations: conversation_id, role, content
- `learner_settings` — Singleton row: active_topic, topic_started_at, words_introduced_in_topic, topic_history_json, tashkeel_mode (always/fade/never), tashkeel_stability_threshold (float, default 30.0)
