# Per-Word Contextual Translations in Sentence Review

**Date**: 2026-02-13
**Status**: Idea — revisit during sentence generation redesign
**Triggered by**: Non-clickable words in review (words with no lemma_id and no function word gloss)

## Current State

### How word glosses work today

The LLM sentence generation returns only sentence-level data:
```python
class SentenceResult(BaseModel):
    arabic: str          # Full diacritized Arabic sentence
    english: str         # Full English translation
    transliteration: str # ALA-LC transliteration
```

Per-word data is produced **deterministically after generation** by `sentence_validator.py`:
- Tokenize the Arabic text
- Strip diacritics + clitics from each token
- Match against the lemma database (`build_lemma_lookup()`)
- Store in `sentence_words` table: position, surface_form, lemma_id, is_target_word

The `sentence_words` table has **no `gloss_en` column**. (In contrast, `story_words` does have one.)

### How glosses reach the frontend

When building review sessions, `sentence_selector.py` resolves glosses at query time:

```python
gloss = lemma.gloss_en if lemma else FUNCTION_WORD_GLOSSES.get(bare)
```

Three fallback levels:
1. **Lemma in DB** → use `Lemma.gloss_en` (dictionary definition)
2. **In FUNCTION_WORD_GLOSSES** → hardcoded dict with ~130 entries (prepositions, pronouns, particles, etc.)
3. **Neither** → `gloss_en: null` sent to frontend

### The gap

Words that fall through all three levels:
- No `lemma_id` because the backfill (`lookup_lemma_id()`) couldn't match the surface form
- Not in `FUNCTION_WORD_GLOSSES` because they're content words, not function words
- Result: the word is either not tappable or shows an empty info card

Examples of how this happens:
- Complex clitic combinations the stripping logic doesn't handle
- Proper nouns not in the lemma database
- Variant forms not covered by `forms_json`
- Words from older sentences before the lemma database was comprehensive

## Function Word Tracking

Function words exist in a gray area:
- **~113 words** in `FUNCTION_WORDS` set (used for validation gating)
- **~130 entries** in `FUNCTION_WORD_GLOSSES` dict (used for frontend glosses)
- **Some have lemma entries** in the DB (from imports) — these are explicitly skipped for FSRS credit in `sentence_review_service.py`
- **Some don't** — they rely entirely on the hardcoded glosses
- A cleanup script (`cleanup_glosses.py`) periodically deletes accidental ULK records for function words

This means function words work either way, but there's no single source of truth. Worth considering whether all function words should have proper lemma entries for consistency.

## The Idea: LLM-Generated Contextual Translations

### Motivation

Instead of just copying dictionary definitions to `sentence_words`, ask the LLM to provide per-word **contextual** translations during sentence generation. This solves two problems:

1. **Coverage**: Every word gets a gloss, regardless of whether it's in the lemma DB
2. **Context-specificity**: A word like عين means "eye" in one sentence and "spring (water)" in another. The dictionary gloss lists both; the contextual gloss gives the right one.

### What changes

**LLM response model** — add per-word breakdown:
```python
class SentenceResult(BaseModel):
    arabic: str
    english: str
    transliteration: str
    word_glosses: dict[str, str]  # surface_form → contextual English gloss
```

**Database** — add column:
```python
class SentenceWord(Base):
    # ... existing columns ...
    gloss_en = Column(Text, nullable=True)  # Contextual gloss from LLM
```

**Sentence selector** — prefer stored contextual gloss:
```python
gloss = sw.gloss_en or (lemma.gloss_en if lemma else FUNCTION_WORD_GLOSSES.get(bare))
```

### Trade-offs

| Pro | Con |
|-----|-----|
| 100% word coverage — no more null glosses | ~20% more output tokens per generation |
| Context-specific meanings | Need to match LLM word keys to tokenized surface forms |
| Learning value: see how a word is used HERE | Existing ~300 active sentences need backfill |
| Generated once, stored forever | LLM might return inconsistent word segmentation |
| No dependency on lemma DB completeness | Quality varies — LLM might gloss particles unhelpfully |

### Matching challenge

The LLM's word segmentation may not match our tokenizer's output. For example:
- LLM might return "بالكتاب" as one key; our tokenizer might strip it to "كتاب"
- LLM might split clitics differently than our `_strip_clitics()`

Solution options:
- Fuzzy matching by stripped form
- Ask LLM to use diacritized forms (matches our stored surface_form)
- Accept partial coverage — unmatched words fall back to lemma gloss

### Interim alternative: Lemma-based backfill

A simpler approach that covers most cases without LLM changes:

1. Add `gloss_en` column to `SentenceWord`
2. At creation time, populate from `Lemma.gloss_en` + `FUNCTION_WORD_GLOSSES`
3. Backfill existing rows via SQL join

This covers ~95% of words (anything with a lemma_id or in the function word dict) but doesn't provide contextual meanings and doesn't help words with no lemma match.

Could be implemented as a stepping stone before the full LLM approach.

## Implementation Timing

Revisit when sentence generation is being redesigned. The prompt changes fit naturally into a generation overhaul. The `gloss_en` column on `SentenceWord` is needed either way (lemma-based or LLM-based) and could be added independently.

## Related Files

- `backend/app/services/llm.py` — LLM prompts and response models
- `backend/app/services/sentence_generator.py` — generation orchestration
- `backend/app/services/sentence_validator.py` — tokenization, `FUNCTION_WORD_GLOSSES`, `map_tokens_to_lemmas()`
- `backend/app/services/sentence_selector.py` — session building, gloss resolution (lines 630, 1089, 1181)
- `backend/app/services/material_generator.py` — sentence storage, `SentenceWord` creation
- `backend/app/models.py` — `SentenceWord` model (no gloss_en), `StoryWord` model (has gloss_en)
- `backend/app/routers/review.py` — recap endpoint gloss resolution (line 421)
