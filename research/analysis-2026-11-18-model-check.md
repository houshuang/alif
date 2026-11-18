# Which models Alif uses, and whether newer ones do better (2026-11-18)

Status: two small fixes shipped (pinned fallback names, a withdrawn scan-retry model).
The sentence-writer finding is a recommendation; nothing about who writes sentences was changed.
Scripts and sentence output: `research/model-check-2026-11-18/` (scan output holds book text and is not committed).

## What was running

| Job | Model | Chosen by |
|---|---|---|
| Sentence writing, dense-sentence generation, memory hooks, mapping checks | Claude Sonnet 5.5 | Claude CLI alias `sonnet` (follows releases) |
| Story writing | Claude Opus 5.5 | Claude CLI alias `opus` |
| Quality review, enrichment, tagging, verification | GPT-6.1 Sol via Codex, then Claude | pinned in `codex_cli.py` (PR #283) |
| API fallback | GPT-5.2 → Claude Haiku 4.5 (→ Opus 4.6 for `opus`) | pinned in `llm.py` |
| Page scans | Gemini 3 Flash preview | pinned in `ocr_service.py` |
| Hard-page retry in book import | `gemini-2.5-flash-preview` | pinned; the name no longer exists |

Server setting `ALIF_CLAUDE_HAIKU_CLI_MODEL=sonnet` sends the "haiku" alias to Sonnet.
The Claude names are what each alias reported about itself on the server.

## Sentence writing: Sonnet 5.5, Opus 5.5 and GPT-6.1 Sol

Method. `sentence_models.py` calls the production `generate_due_menu_material(dry_run=True)`
with the generation call routed to one model per arm. Every arm in a run gets the same
four menus of 150 due words (same random seed) and goes through the production validator,
quality review and mapping verification. Nothing is written. Two runs, with different menus
and different judges for the quality review:

| Run | Judge | Writer | Returned | Valid | Passed review | Stored (survived mapping check) | Due words covered |
|---|---|---|---|---|---|---|---|
| 1 | GPT-6.1 Sol | Sonnet 5.5 | 48 | 46 | 41 | 25 | 121 |
| 1 | GPT-6.1 Sol | Opus 5.5 | 48 | 45 | 42 | 24 | 109 |
| 1 | GPT-6.1 Sol | GPT-6.1 Sol | 48 | 45 | 44 | 31 | 132 |
| 2 | Sonnet 5.5 | Sonnet 5.5 | 48 | 46 | 19 | 14 | 66 |
| 2 | Sonnet 5.5 | Opus 5.5 | 49 | 44 | 25 | 16 | 79 |
| 2 | Sonnet 5.5 | GPT-6.1 Sol | 48 | 46 | 32 | 24 | 103 |

Totals: GPT-6.1 Sol stored 55 of 96 sentences (235 due words), Sonnet 39 of 96 (187),
Opus 40 of 97 (188). GPT led under both judges, including the run where Sonnet judged.
Generation time per call: Sonnet about 85 s, Opus about 95 s, GPT about 150 s.

Reading the output confirms the direction. Sonnet's rejected sentences contain real
Arabic errors (`تَوَفَّى الجَارُ` for `تُوُفِّيَ`, `سِيَّمَا` without `لا`, `مَدَّ قَرْنٍ`) and forced
scenes; GPT tends toward plainer nominal sentences with fewer errors; Opus reads more
natural than Sonnet but loses as many at the mapping check. The May 2026 test that keeps
Codex away from sentence writing compared two models that are no longer in use.

Limits. 96 sentences per writer, one learner state, the dense-sentence prompt only.
The ordinary per-word prompts were not tested. Codex failed about one call in six on
the day of the test, so any switch needs Sonnet as fallback.

Side finding. The judge matters more than the writer: GPT-6.1 Sol passed 127 of 136
valid sentences, Sonnet 5.5 passed 76 of 136 (different menus, so not a clean
comparison). In production the judge is GPT with Sonnet as fallback, so the pass rate
depends on which provider answers.

Most sentences lost at the mapping check fail with "correct lemma not found": the
writer used a verb or derived form whose own entry is not in the vocabulary.

## Page scans: Gemini 3 Flash preview against newer Gemini models

Method. `scan_models.py` runs the production snap/pre-read call
(`extract_text_and_translation`) on saved page photos. Round 1: four models on ten
pages. Round 2: the current model and the best candidate on 26 more pages. No ground
truth exists, so disagreements were counted and a sample checked against the photos.

| Model | Pages | Failures | Average seconds | Cost per page | Word errors seen (round 1) |
|---|---|---|---|---|---|
| Gemini 3 Flash preview (current) | 36 | 0 | 6.5–8.2 | about $0.0075 | 2, plus one sentence dropped on a shadowed textbook page |
| Gemini 3.6 Flash | 10 | 0 | 9.6 | not published on the price page read | 6 |
| Gemini 3.8 Flash | 10 | 0 | 15.2 | about $0.009 (doubles 1 Jan 2027) | 3 |
| Gemini 4 Hydrogen | 36 | 0 | 13.1–15.9 | about $0.025 | 0 |

Round 2: the current model and Gemini 4 Hydrogen differ on 6 of 1,775 words. Two were
checked against the photos: one error each (`باسمه` for printed `باسه` by the current
model; `فأكل` for printed `فآكل` by Hydrogen). Hydrogen also writes spaces around
punctuation as printed.

Conclusion. The current model is as accurate as the newer ones on ordinary pages, twice
as fast and a third of the price. Kept. Gemini 3.6 Flash, which Google names as the
replacement for the preview model, made the most errors; if the preview model is
withdrawn, move to Gemini 4 Hydrogen, not 3.6. Hydrogen now serves the hard-page retry
in book import, replacing a withdrawn name that made that retry fail every time.

## Fallback chain

Tested through litellm on the server before changing. GPT-6.x and Claude 5 models
reject a caller-set `temperature`, so a plain rename would have made every fallback
call fail. The API chain now sends no temperature. New pins: `gpt-6.1-sol` (same model
as the Codex primary), `claude-haiku-5-5` (also serves `/api/chat/ask`; about 1 s on a
short prompt, same as Haiku 4.5), `claude-opus-5-5`.
