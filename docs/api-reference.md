# API Reference

Full endpoint list. See `backend/app/routers/` for implementation.

## Words
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/words?limit=50&status=learning&category=function&sort=most_seen` | List words with knowledge state. category: function\|names. sort=most_seen orders by times_seen descending. Returns last_ratings (last 8 review ratings for sparkline) and knowledge_score. |
| GET | `/api/words/{id}` | Word detail with review stats + root family + review history. Returns `source_info` based on `ulk.source` (how the word was introduced to learning: book/story_import/duolingo/textbook_scan) with fallback to `lemma.source` (lexical data origin: wiktionary/avp_a1/etc) for generic ULK sources. |
| POST | `/api/words/{lemma_id}/suspend` | Suspend a word (stops appearing in reviews) |
| POST | `/api/words/{lemma_id}/unsuspend` | Reactivate a suspended word with fresh FSRS card |

## Review
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/review/next?limit=10` | Due review cards (legacy word-only) |
| GET | `/api/review/next-listening` | Listening-suitable review cards (legacy) |
| GET | `/api/review/next-sentences?limit=10&mode=reading` | Sentence-centric review session (primary) |
| POST | `/api/review/submit` | Submit single-word review (legacy) |
| POST | `/api/review/submit-sentence` | Submit sentence review — all words get FSRS credit. Accepts confused_lemma_ids |
| POST | `/api/review/undo-sentence` | Undo a sentence review — restores pre-review FSRS state, deletes logs |
| GET | `/api/review/word-lookup/{lemma_id}` | Word detail + root family for review lookup |
| POST | `/api/review/sync` | Bulk sync offline reviews |
| POST | `/api/review/reintro-result` | Submit re-introduction quiz result |
| POST | `/api/review/wrap-up` | Wrap-up mini-quiz: word-level recall cards for acquiring words seen in current micro-session |
| POST | `/api/review/recap` | (Deprecated) Was next-session recap — removed from frontend, redundant with within-session repetition |
| POST | `/api/review/warm-sentences` | Pre-generate sentences for likely next session words (background, returns 202) |

## Learn
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/learn/next-words?count=5` | Best next words to introduce |
| POST | `/api/learn/introduce` | Introduce word (create FSRS card + trigger sentence generation) |
| POST | `/api/learn/introduce-batch` | Batch introduce |
| GET | `/api/learn/root-family/{root_id}` | Words from a root with knowledge state |
| POST | `/api/learn/quiz-result` | Submit learn-mode quiz result |
| POST | `/api/learn/suspend` | Suspend a word (never show again) |
| GET | `/api/learn/sentences/{lemma_id}` | Poll for generated sentence (ready/not ready) |
| GET | `/api/learn/sentence-params/{lemma_id}` | Max words + difficulty hint for sentence generation |

## Grammar
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/grammar/features` | All 24 grammar features with categories |
| GET | `/api/grammar/progress` | User's grammar exposure/comfort per feature |
| GET | `/api/grammar/unlocked` | Current tier and unlocked grammar features |
| GET | `/api/grammar/lesson/{key}` | Get grammar lesson content for a feature |
| POST | `/api/grammar/introduce` | Introduce a grammar feature |
| GET | `/api/grammar/confused` | List grammar features causing confusion |

## Stats
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stats` | Basic stats (total, known, learning, due) |
| GET | `/api/stats/analytics` | Full analytics (pace, CEFR estimate, daily history) |
| GET | `/api/stats/cefr` | CEFR reading level estimate |
| GET | `/api/stats/deep-analytics` | Deep analytics: stability distribution, retention 7d/30d, transitions today/7d/30d, comprehension 7d/30d, struggling words, root coverage, recent sessions, acquisition pipeline, insights (encounters-to-graduation, graduation rate, reading time, strongest/most-encountered word, avg stability, best weekday, dark horse root, unique sentences, forgetting forecast) |

## Stories
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stories` | List all stories |
| GET | `/api/stories/{id}` | Story detail with words |
| POST | `/api/stories/generate` | Generate story (LLM) |
| POST | `/api/stories/import` | Import Arabic text as story |
| POST | `/api/stories/{id}/complete` | Complete story (FSRS credit for all words) |
| POST | `/api/stories/{id}/skip` | Skip story |
| POST | `/api/stories/{id}/too-difficult` | Mark story too difficult |
| POST | `/api/stories/{id}/lookup` | Look up word in story |
| GET | `/api/stories/{id}/readiness` | Recalculate readiness |

## Sentences & Analysis
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/sentences/generate` | Generate sentence for a target word |
| POST | `/api/sentences/validate` | Validate sentence against known vocabulary |
| GET | `/api/sentences/{id}/info` | Sentence debug info: metadata, review history, per-word FSRS difficulty |
| POST | `/api/analyze/word` | Analyze word morphology (CAMeL Tools or stub fallback) |
| POST | `/api/analyze/sentence` | Analyze sentence morphology (CAMeL Tools or stub fallback) |

## TTS
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tts/speak/{text}` | Generate TTS audio (async) |
| GET | `/api/tts/voices` | List available TTS voices |
| POST | `/api/tts/generate` | Generate TTS audio (async) |
| POST | `/api/tts/generate-for-sentence` | Generate sentence TTS with slow mode |
| GET | `/api/tts/audio/{cache_key}.mp3` | Serve cached audio file |

## OCR
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ocr/scan-pages` | Upload textbook page images for OCR word extraction (multipart, background processing) |
| GET | `/api/ocr/batch/{batch_id}` | Get batch upload status with per-page results |
| GET | `/api/ocr/uploads` | List recent upload batches with results |
| POST | `/api/ocr/extract-text` | Extract Arabic text from image for story import (synchronous) |

## Other
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/import/duolingo` | Run Duolingo import |
| POST | `/api/flags` | Flag content for LLM re-evaluation |
| GET | `/api/flags` | List content flags (optional ?status= filter) |
| GET | `/api/activity` | Recent activity log entries |
| POST | `/api/chat/ask` | Ask AI a question (with learning context) |
| GET | `/api/chat/conversations` | List conversation summaries |
| GET | `/api/chat/conversations/{id}` | Full conversation messages |
