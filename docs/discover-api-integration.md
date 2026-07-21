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
{ "text": "<Arabic text>", "count": 8, "selection": "common_first", "include_oov": false }
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `text` | string | — | Arabic prose. Other scripts/punctuation are ignored. |
| `count` | integer | `8` | Max suggestions to return. Clamped to **1–50**. |
| `selection` | string | `"common_first"` | Ranking mode. `"common_first"` = generally-common words first (best for "what to learn next"). `"distinctive"` = the words that *carry this text* — frequent here but rare in general (TF-IDF-style lift), best for a text-attached glossary. |
| `include_oov` | bool | `false` | When `true`, keep out-of-vocabulary words (dialect / slang / vulgar / loanwords) that the morphological analyzer can't resolve, via a clitic-stripped **surface fallback**, instead of dropping them. Off by default, so the "learn next" use case isn't polluted. Turn it on for glossing authentic dialectal/literary text. |

**Response**

```json
{
  "words": [
    {
      "surface": "كسها",
      "surface_forms": ["كسها", "وكسها", "والكس"],
      "lemma_ar": "كُسّ",
      "lemma_ar_bare": "كس",
      "root": "ك س س",
      "gloss_en": "vulva; cunt",
      "pos": "noun",
      "register": "vulgar",
      "dialect": "gulf",
      "transliteration": "kuss",
      "freq_rank": null,
      "count_in_text": 22,
      "example_ar": "وضعه في كسها فأحس أنها فرحت",
      "lemma_source": "surface_fallback"
    }
  ],
  "count": 1
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `surface` | string | The most frequent form as it appeared in the text. |
| `surface_forms` | string[] | All attested forms, by descending count (cliticized variants the reader will meet). |
| `lemma_ar` | string | Diacritized citation lemma (dictionary form). |
| `lemma_ar_bare` | string | Normalized, diacritic-free lemma — the **stable identity key**. Echo this back to `/add`. |
| `root` | string \| null | Space-separated Semitic root when available; `null` for surface-fallback OOV words. |
| `gloss_en` | string \| null | Concise English meaning, in the text's context (null only if glossing failed). |
| `pos` | string | `noun` / `verb` / `adjective` / `adverb` / `particle` / `proper_noun`. |
| `is_proper_noun` | bool | Whether the gloss model judged this a name. In glossary mode (`include_oov`) names are **returned flagged** (so the model's over-tagging of vulgar/dialect nouns as "proper" can't silently drop content); filter on this if you don't want names. |
| `register` | string \| null | `neutral` / `literary` / `colloquial` / `vulgar` / `clinical`. |
| `dialect` | string \| null | `msa` / `gulf` / `egyptian` / `levantine` / `mixed`. |
| `transliteration` | string \| null | ALA-LC romanization. |
| `freq_rank` | integer \| null | Position in Alif's MSA frequency list (lower = more common); `null` if outside the list. |
| `count_in_text` | integer | Occurrences of this lemma (all forms) in the submitted text. |
| `example_ar` | string \| null | One short attested clause containing the word, for display. |
| `lemma_source` | string | `"camel"` (resolved by the morphological analyzer) or `"surface_fallback"` (clitic-stripped OOV — root/pos are best-effort and the gloss is the authority). |

Words already known to Alif and function words never appear. Proper nouns are dropped
in the default (learn-next) mode; in glossary mode (`include_oov`) they are returned
with `is_proper_noun: true` so you can keep or filter them yourself.

**Glossing of OOV / dialectal / vulgar words** (`include_oov: true`): the gloss step
runs *in context* (it sees an example clause), so it glosses the word as used, assigns
`register`/`dialect`, and can **correct** a wrong automatic lemma. Vulgar and slang
terms are glossed clinically and accurately, not censored. Note: cross-form aggregation
of OOV *verb* conjugations is best-effort (Arabic hollow/weak verbs can't be unified
without full morphology) — nouns with clitics (كسها/الكس/وكسها → كس) do aggregate.

---

### 2. `POST /api/discover/add` — add one word

Creates the word (if new) and introduces it into the learner's queue **immediately**.
Example-sentence generation and enrichment happen in the background.

The existence check is **citation-strict** (since 2026-07-15): the submitted
`lemma_ar_bare` matches an existing lemma only exactly, via a registered spelling
variant, or with a definite-article prefix (`المكتبة`/`بالمكتبة` → `مكتبة`). It never
guesses via single-letter clitic stripping or fuzzy morphology — so a genuinely new
word that merely *looks like* clitic+known-word (كناس "street sweeper" vs ك+ناس) is
correctly created instead of silently matching the wrong lemma.

**Request** — echo back the object you got from `/words` (only `lemma_ar_bare` is
strictly required, but send what you have):

```json
{
  "lemma_ar_bare": "كس",
  "lemma_ar": "كُسّ",
  "gloss_en": "vulva; cunt",
  "pos": "noun",
  "transliteration": "kuss",
  "register": "vulgar",
  "dialect": "gulf",
  "source": "bookifier"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `lemma_ar_bare` | ✅ | Identity key from `/words`. |
| `lemma_ar` | optional | Diacritized form; falls back to the bare form if omitted. |
| `gloss_en` | ✅ for **new** words | Must be non-empty to create a new word (Alif never stores a word without an English gloss). Ignored if the word already exists. |
| `pos` | optional | — |
| `transliteration` | optional | — |
| `register` | optional | `neutral`/`literary`/`colloquial`/`vulgar`/`clinical` — persisted on the word so dialectal/vulgar vocabulary stays distinguishable in Alif. |
| `dialect` | optional | `msa`/`gulf`/`egyptian`/`levantine`/`mixed` — persisted on the word. |
| `source` | optional | Provenance tag identifying the **calling consumer** (e.g. `bookifier`). Persisted on both the lemma row and its scheduling record, and stamped on the `dragoman_word_added` interaction log, so words from different consumers stay distinguishable in later analysis. Defaults to `dragoman` (the in-app discover screen) when omitted. |

**Response `200`**

```json
{
  "lemma_id": 5821,
  "lemma_ar": "أَعْلَن",
  "gloss_en": "announce",
  "source": "bookifier",
  "created": true,
  "state": "acquiring",
  "already_known": false,
  "sense_rerouted_from": null
}
```

| Field | Meaning |
|-------|---------|
| `lemma_id` | Alif's internal id for the (canonical) word. |
| `source` | The provenance tag that was recorded (the value sent, or `dragoman` if none was sent). |
| `created` | `true` if a new word was created; `false` if it already existed and was just (re)introduced. |
| `state` | Learning state after the call — normally `acquiring` (now in the active queue). |
| `already_known` | `true` if the learner already knew this word. |
| `sense_rerouted_from` | Normally `null`. Set to a lemma_id when the bare-form lookup landed on a same-skeleton homograph whose gloss/POS conflicted with what you sent (e.g. sending مَلِك "king" while Alif's ملك is مَلَك "angel"): the add was re-routed to a sense-compatible sibling or created as a new word instead, and this field records the lemma it was steered away from. Purely informational — the word in `lemma_id` is the one that was added. |

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

### 4. `POST /api/discover/snap` — photo → translation + words (read-only)

The in-app **snap-to-read** path: instead of supplying text, send a photo of an Arabic
page. The server OCRs **and** translates it in one Gemini Vision call, then runs the same
word-discovery as `/words` on the extracted text. Synchronous (~5-8s end to end).

**Request** — `multipart/form-data` with a `file` (image, ≤20MB). Query params:
`count` (default 5, ≤50), `selection` (`common_first` default | `distinctive`),
`include_oov` (default `true` — keep archaic/dialectal words in authentic texts).

**Response `200`**

```json
{
  "arabic_text": "… the OCR'd Arabic …",
  "translation_en": "… a faithful English translation …",
  "words": [ { "lemma_ar": "…", "lemma_ar_bare": "…", "gloss_en": "…", "...": "same shape as /words" } ],
  "count": 5
}
```

`422` when no Arabic text is found in the image; `502` on OCR/translation failure.
Add chosen words with `/add` or `/add-batch` exactly as for `/words` (the snap UI tags
them `source="snap"`).

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
