# Data Model

SQLAlchemy models in `backend/app/models.py`. Pydantic schemas in `backend/app/schemas.py`.

## Core Tables
- `roots` — 3/4 consonant roots: core_meaning, productivity_score
- `lemmas` — Dictionary forms: root FK, pos, gloss, frequency_rank, cefr_level, grammar_features_json, forms_json, example_ar/en, transliteration, audio_url, canonical_lemma_id (variant FK), source_story_id, word_category (NULL=standard, proper_name, onomatopoeia), thematic_domain, etymology_json, memory_hooks_json, wazn (morphological pattern e.g. "fa'il", "maf'ul", "form_2", indexed), wazn_meaning (human-readable pattern description)
- `user_lemma_knowledge` — Per-lemma SRS state: knowledge_state (encountered/acquiring/new/learning/known/lapsed/suspended), fsrs_card_json, times_seen, times_correct, total_encounters, source (study/duolingo/textbook_scan/book/story_import/auto_intro/collateral/leech_reintro — preserved through acquisition, not overwritten), variant_stats_json, acquisition_box (1/2/3), acquisition_next_due, entered_acquiring_at (when word entered Leitner pipeline), graduated_at, leech_suspended_at, leech_count

## Sentences & Reviews
- `sentences` — Generated/imported: target_lemma_id, story_id (FK to stories, for book-extracted sentences), source (llm/book/tatoeba/manual), times_shown, last_reading_shown_at/last_listening_shown_at, last_reading_comprehension/last_listening_comprehension, is_active, max_word_count, created_at, page_number (for book sentences)
- `sentence_words` — Word breakdown: position, surface_form, lemma_id, is_target_word, grammar_role_json
- `review_log` — Review history: rating 1-4, mode, sentence_id, credit_type (metadata only), is_acquisition, fsrs_log_json (pre-review snapshots for undo)
- `sentence_review_log` — Per-sentence review: comprehension, timing, session_id

## Grammar
- `grammar_features` — 24 features across 5 categories
- `sentence_grammar_features` — Sentence ↔ grammar junction
- `user_grammar_exposure` — Per-feature: times_seen, times_correct, comfort_score

## Stories & Content
- `stories` — title_ar/en, body_ar/en, transliteration, source (generated/imported/book_ocr), status (active/completed/suspended), readiness_pct, difficulty_level, page_count (for book imports). API returns page_readiness array (per-page new_words/learned_words/unlocked — counts only words unknown at import time, using acquisition_started_at vs story.created_at), new_total/new_learning (deduplicated story-level counts), and sentences_seen for book_ocr stories.
- `story_words` — Per-token: position, surface_form, lemma_id, gloss_en, is_function_word, name_type
- `page_uploads` — OCR tracking: batch_id, status, extracted_words_json, new_words, existing_words, textbook_page_number (detected printed page number from OCR)

## System
- `content_flags` — Flagged content: content_type, status (pending/reviewing/fixed/dismissed)
- `activity_log` — System events: event_type, summary, detail_json
- `variant_decisions` — LLM variant cache: word_bare, base_bare, is_variant, reason
- `chat_messages` — AI conversations: conversation_id, role, content
- `learner_settings` — Singleton row: active_topic, topic_started_at, words_introduced_in_topic, topic_history_json, tashkeel_mode (always/fade/never), tashkeel_stability_threshold (float, default 30.0)
