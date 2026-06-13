# Alif Discover API — Integration Guide

Turn a block of Arabic text into vocabulary a reader can add to their Alif learning
queue. Built for external apps (e.g. the **Dragoman** bilingual magazine): show the
reader an Arabic piece, ask Alif which words are worth learning, render "add to Alif"
buttons, and POST the chosen words back.

This is the contract external services should code against. Implementation lives in
`backend/app/routers/discover.py`.

---

## Base URL & access

- **Backend base:** `http://alifstian.duckdns.org:3000`
- All endpoints are under `/api/discover`.
- **No authentication.** Alif is single-user; there are no API keys or tokens.
- **CORS:** all origins allowed (`Access-Control-Allow-Origin: *`), all methods, all
  headers — you can call it directly from browser JavaScript.
- **HTTPS:** the backend itself is HTTP on port 3000. If your client page is served
  over HTTPS, browsers will block a plain-HTTP request (mixed content). Put an HTTPS
  reverse-proxy route in front of `:3000` on your side (the Dragoman deployment does
  this in its own nginx). Nothing changes in Alif for this.
- **Content type:** send `Content-Type: application/json`; responses are JSON.

---

## How it works (and why it's cheap on large text)

`/words` is designed so you can send a whole article. The expensive step (an LLM) runs
**once**, only on the final shortlist — never per word:

1. Tokenize the text (Arabic-letter runs; punctuation/digits ignored).
2. Drop function words and any word **already in Alif's vocabulary**. Identity goes
   through Alif's hardened lemma lookup, which strips clitics and resolves spelling
   variants to their canonical form — so `المكتبة`, `وبالمكتبة`, etc. all resolve to the
   known lemma `مكتبة` and are correctly excluded.
3. Lemmatize the remaining *unknown* words with **CAMeL** (a statistical morphological
   analyzer, not an LLM), grouping inflections under one citation lemma.
4. Rank: words in Alif's MSA frequency list first (most frequent first), then by how
   often they appear in your text.
5. Take the top `count`, then make **one** LLM call to gloss just those (English gloss,
   POS, transliteration, proper-noun flag). Proper nouns are dropped.

Cost is bounded by `count`, not by text length. Send a reasonable chunk (an article, a
chapter); there is currently no hard size cap, so don't post tens of megabytes at once.

The whole flow is asynchronous-friendly: `/words` typically takes a couple of seconds
(one LLM call), and `/add` returns immediately while heavier work (quality gates,
example-sentence generation) runs in the background.

---

## Endpoints

### 1. `POST /api/discover/words` — suggest words to learn (read-only)

Returns the highest-value lemmas in the text that aren't in Alif yet.

**Request**

```json
{ "text": "<Arabic text>", "count": 8 }
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `text` | string | — | Arabic prose. Other scripts/punctuation are ignored. |
| `count` | integer | `8` | Max suggestions to return. Clamped to **1–20**. |

**Response**

```json
{
  "words": [
    {
      "surface": "أعلنت",
      "lemma_ar": "أَعْلَن",
      "lemma_ar_bare": "اعلن",
      "gloss_en": "announce",
      "pos": "verb",
      "transliteration": "aʿlana",
      "freq_rank": null,
      "count_in_text": 1
    }
  ],
  "count": 1
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `surface` | string | A form as it appeared in the text. |
| `lemma_ar` | string | Diacritized citation lemma (dictionary form). |
| `lemma_ar_bare` | string | Normalized, diacritic-free lemma — the **stable identity key**. Echo this back to `/add`. |
| `gloss_en` | string \| null | Concise English meaning (null only if glossing failed). |
| `pos` | string | `noun` / `verb` / `adjective` / `adverb` / `particle`. |
| `transliteration` | string \| null | ALA-LC romanization. |
| `freq_rank` | integer \| null | Position in Alif's MSA frequency list (lower = more common); `null` if outside the list. |
| `count_in_text` | integer | Occurrences of this lemma in the submitted text. |

Words already known to Alif, function words, and proper nouns do **not** appear.

---

### 2. `POST /api/discover/add` — add one word

Creates the word (if new) and introduces it into the learner's queue **immediately**.
Example-sentence generation and enrichment happen in the background.

**Request** — echo back the object you got from `/words` (only `lemma_ar_bare` is
strictly required, but send what you have):

```json
{
  "lemma_ar_bare": "اعلن",
  "lemma_ar": "أَعْلَن",
  "gloss_en": "announce",
  "pos": "verb",
  "transliteration": "aʿlana"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `lemma_ar_bare` | ✅ | Identity key from `/words`. |
| `lemma_ar` | optional | Diacritized form; falls back to the bare form if omitted. |
| `gloss_en` | ✅ for **new** words | Must be non-empty to create a new word (Alif never stores a word without an English gloss). Ignored if the word already exists. |
| `pos` | optional | — |
| `transliteration` | optional | — |

**Response `200`**

```json
{
  "lemma_id": 5821,
  "lemma_ar": "أَعْلَن",
  "gloss_en": "announce",
  "created": true,
  "state": "acquiring",
  "already_known": false
}
```

| Field | Meaning |
|-------|---------|
| `lemma_id` | Alif's internal id for the (canonical) word. |
| `created` | `true` if a new word was created; `false` if it already existed and was just (re)introduced. |
| `state` | Learning state after the call — normally `acquiring` (now in the active queue). |
| `already_known` | `true` if the learner already knew this word. |

**Response `400`** — the request was rejected (response body: `{"detail": "<reason>"}`):

- creating a **new** word with an empty `gloss_en`, or
- a word flagged as a **proper noun** (names aren't vocabulary).

---

### 3. `POST /api/discover/add-batch` — add several words

Same behavior as `/add`, per word. Each word is committed independently, so one bad
word never discards the others; a word repeated within the batch is added once.

**Request**

```json
{ "words": [ { "lemma_ar_bare": "اعلن", "gloss_en": "announce", "pos": "verb" },
             { "lemma_ar_bare": "متجدد", "gloss_en": "renewable", "pos": "noun" } ] }
```

**Response `200`**

```json
{
  "added": [
    { "lemma_id": 5821, "lemma_ar": "أَعْلَن", "gloss_en": "announce", "created": true, "state": "acquiring", "already_known": false },
    { "lemma_ar_bare": "مصر", "error": "refusing to add proper noun 'مصر'" }
  ],
  "count": 2
}
```

`added` preserves request order. Each entry is either the success shape (as in `/add`)
or `{ "lemma_ar_bare": "...", "error": "..." }` for a rejected word. The whole call
returns `200` even when individual words fail — inspect each entry.

---

## Recommended integration flow

1. On rendering an Arabic piece, `POST /api/discover/words` with the article text and a
   `count` (8–12 reads well in a sidebar).
2. Render each returned word as an "add to Alif" control, showing `lemma_ar` + `gloss_en`.
3. On click, `POST /api/discover/add` with that word object. On a "add all" button, use
   `/api/discover/add-batch`.
4. Treat `created`/`already_known` for UI feedback ("Added" vs "Already learning").

You don't need to track state yourself: re-running `/words` later naturally stops
suggesting words the reader has since added (they're now in Alif's vocabulary).

---

## Notes & guarantees

- **Idempotent-ish adds:** adding a word that already exists just (re)introduces it;
  it won't create duplicates, and variants/spellings resolve to one canonical word.
- **No bad data enters review:** new words pass through Alif's standard quality gates and
  example-sentence generation in the background before they surface as study material.
- **Proper nouns and function words are filtered** out of suggestions automatically.
- **Material isn't instant:** a freshly-added word is in the queue immediately, but its
  practice sentences are generated in the background (seconds to a few minutes).
- **Arabic only.** The pipeline relies on Arabic morphology (CAMeL, clitic stripping,
  Semitic roots).

---

## cURL examples

```bash
# Suggest up to 5 new words from an article
curl -s -X POST http://alifstian.duckdns.org:3000/api/discover/words \
  -H 'Content-Type: application/json' \
  -d '{"text":"أعلنت الحكومة عن خطة جديدة لتطوير الاقتصاد الوطني","count":5}'

# Add one of them
curl -s -X POST http://alifstian.duckdns.org:3000/api/discover/add \
  -H 'Content-Type: application/json' \
  -d '{"lemma_ar_bare":"اعلن","lemma_ar":"أَعْلَن","gloss_en":"announce","pos":"verb"}'
```
