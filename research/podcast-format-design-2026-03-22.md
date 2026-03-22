# AI-Generated Arabic Learning Podcast: Format Design

> **Date**: 2026-03-22
> **Context**: Designing a personalized audio learning experience for a single learner walking 30 minutes. System has full SRS data (FSRS stability, acquisition state, root/pattern knowledge, ~500+ lemmas tracked). No phone interaction allowed.

---

## Research Foundations

### Within-Session Repetition Spacing

**Pimsleur's graduated interval recall** uses: 5s, 25s, 2min, 10min, 1hr within a session. The first four intervals fit inside a 30-minute episode. This means a word introduced at minute 0 should recur at roughly minute 0.5, minute 2, and minute 12.

**Nakata (2017)** found that 5 retrievals per word produced significantly better retention than 1 or 3 retrievals. The sweet spot from your own scheduling research (Nakata 2017 in `scheduling-system.md`) is **3 within-session retrievals** for the efficiency/time tradeoff.

**Expanding vs. equal spacing**: Nakata (2015) found no statistically significant difference between expanding (01133) and equal (03333) spacing schedules for vocabulary. Practical implication: expanding spacing (item returns after 1, 3, 8 intervening items) is slightly favored because it catches fragile memories early.

### Comprehensibility Thresholds for Listening

- **95% lexical coverage** = minimum for adequate comprehension (Laufer 1989, Nation 2006). That is 1 unknown word per 20 running words.
- **98% coverage** = comfortable comprehension. 1 unknown in 50 words.
- For **listening** specifically, research suggests 95-98% is needed, possibly relaxed to 90% for informal/contextualized input (van Zeeland & Schmitt 2012).
- **Your system's 60% comprehensibility gate is for reading**. Listening needs to be tighter — closer to 90-95% known words among scaffold/context words. The existing `listening.py` enforces `MIN_LISTENING_STABILITY_DAYS = 7.0` and `MIN_REVIEWS_FOR_LISTENING = 3`, which is already conservative.

### How Many New Words Per 30-Minute Episode?

- Pimsleur introduces **~8-12 new words per 30-minute lesson**
- Glossika targets **5-8 new sentence patterns per session**
- Michel Thomas introduces **6-10 new building blocks per session** (often morphemes/patterns rather than isolated words)
- Research on dose rate (Justice et al. 2020): 6 target words with 45 exposures total per 30 minutes = effective for acquisition
- **Conservative recommendation for audio-only (no visual support): 5-8 new words**, each heard in 3-5 different contexts, with 3+ retrievals per word spaced across the episode

### Known-to-New Ratio

For purely aural comprehension without visual support:
- **Minimum**: 90% known tokens in any Arabic passage (1 unknown per 10 words)
- **Optimal**: 95% known tokens (1 unknown per 20 words)
- **With English glossing/translation**: can tolerate lower coverage since meaning is scaffolded
- In a ~7-word Arabic sentence, this means **at most 1 unknown word** per sentence (at 85% = ~1 unknown), ideally 0-1 unknowns

### Engagement Research

- **Narrative superiority**: stories activate more brain regions than isolated facts (Hasson et al. 2012)
- **Prediction drives retention**: the brain encodes better when it has made a prediction and gets feedback (testing effect)
- **Emotional engagement**: material with emotional valence is retained ~25% better
- **Cognitive load**: passive listening has lower working memory engagement than active recall — formats that prompt mental activity (even without phone interaction) dramatically improve retention

---

## Format 1: Sentence Drill (Glossika-Style)

### Concept
Pure sentence-based review with Pimsleur-style graduated recall. No narrative thread. Each sentence is a standalone learning unit. The system selects sentences from the existing pre-generated pool, prioritizing words due for review.

### Minute-by-Minute Structure (30 min)

```
00:00-01:00  Opening jingle + brief orientation in English
             "Episode 47. Today we're working with 28 sentences.
              Six new words, twenty-two you've seen before."

01:00-06:00  BLOCK A — First 5 sentences (new words introduced)
             Per sentence:
               [Arabic at 0.7x speed] → [2s pause] → [English] → [1.5s pause] → [Arabic at normal speed]
             ~1 minute per sentence

06:00-07:30  BLOCK A ECHO — Replay all 5 Arabic sentences back-to-back, no English
             [Arabic 1] → [3s] → [Arabic 2] → [3s] → ... → [Arabic 5]

07:30-12:30  BLOCK B — Next 5 sentences (mix of review + 1-2 new words)
             Same per-sentence format as Block A

12:30-14:00  BLOCK A RECALL — Replay Block A with English FIRST
             [English] → [4s pause for mental recall] → [Arabic]
             This is the "test" — learner tries to anticipate the Arabic

14:00-15:00  BLOCK B ECHO — All 5 Arabic sentences back-to-back

15:00-20:00  BLOCK C — Next 5 sentences (mostly review, high-stability words)

20:00-21:30  BLOCK B RECALL — English-first format for Block B

21:30-23:00  BLOCK C ECHO — All 5 back-to-back

23:00-26:00  INTERLEAVED RECALL — Random selection of 8 sentences from A+B+C
             [English] → [4s pause] → [Arabic]

26:00-28:30  FULL ARABIC STREAM — All 15 sentences in Arabic only, natural pacing
             No English. Pure listening comprehension test.

28:30-30:00  Closing: "You heard 6 new words today: [word] meaning [gloss], ..."
             + brief preview of tomorrow's focus
```

### Arabic/English Mix
- Phase 1 (introduction): Arabic → English → Arabic (sandwich)
- Phase 2 (echo): Arabic only
- Phase 3 (recall): English → pause → Arabic (reverse direction, testing)
- Phase 4 (stream): Arabic only

### Repetition Pattern
Each sentence is heard **5 times**:
1. Introduction (Arabic slow) — minute N
2. Introduction (Arabic normal) — minute N+0.3
3. Echo replay — minute N+5
4. Recall test — minute N+12
5. Full stream — minute N+25

New words get an additional isolated mention at the closing.

### Vocabulary Introduction
- 5-8 new words, always introduced in sentences where they are the only unknown
- Sentence selected to have 90%+ known scaffold words (leveraging `listening.py` confidence scoring)
- New words are front-loaded in Block A and sprinkled into Block B
- Block C is pure review (all known words, high stability)

### Pacing
- Arabic slow: 0.7x speed (existing TTS setting)
- Arabic normal: 1.0x speed
- Pauses: 2s after slow Arabic, 1.5s after English, 4s during recall (mental processing)
- Total time per sentence cycle: ~55-65 seconds
- Sentence gap in echo/stream: 3s

### Assessment

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Passive suitability | 9/10 | No interaction needed. Recall pauses prompt mental activity. |
| Memory effectiveness | 7/10 | Strong within-session spacing. Lacks narrative glue for deeper encoding. |
| Engagement | 4/10 | Repetitive by design. Can feel like a treadmill after 15 minutes. |
| Difficulty calibration | 9/10 | Directly uses SRS data. Each sentence's scaffold is pre-scored. |
| Production complexity | 6/10 | Straightforward sequencing. Needs English TTS + Arabic TTS + silence stitching. |

### Pros
- Most directly aligned with SRS data — every sentence earns review credit
- Predictable structure lets the brain settle into a rhythm
- Can be precisely calibrated to the learner's current level
- Sentences already exist in the DB — minimal new generation needed
- Review credit can be logged: each sentence heard = partial FSRS review

### Cons
- Boring. The #1 failure mode for audio learning is the learner mentally checking out
- No narrative context means words are encoded with weaker memory traces
- Difficult to maintain attention for 30 minutes of disconnected sentences
- Doesn't leverage the root/pattern system that makes Arabic vocabulary systematic
- "Schoolwork" feeling conflicts with the walking/relaxation context

---

## Format 2: Story Breakdown (Michel Thomas Meets Audiobook)

### Concept
A short story (8-15 sentences) is the episode's backbone. The story is generated using known vocabulary + target new words, using the existing story generation pipeline (Opus, with vocabulary constraints). The episode teaches the story sentence by sentence, then replays it as a continuous narrative.

### Minute-by-Minute Structure (30 min)

```
00:00-01:00  Opening + English story premise
             "Today's story: A teacher in Damascus discovers that his
              favorite cafe has been replaced by a bookstore. But
              something about this bookstore isn't quite right..."

01:00-02:30  SENTENCE 1 — Build-up
             [English sentence] → [1s]
             [Arabic — first clause only, slow] → [1s] → [English gloss of clause]
             [Arabic — second clause only, slow] → [1s] → [English gloss of clause]
             [Full Arabic sentence, slow] → [2s]
             [Full Arabic sentence, normal speed]

02:30-04:00  SENTENCE 2 — Build-up (same pattern)

04:00-04:30  REPLAY 1-2 — Both sentences in Arabic, natural speed
             Brief English connector between if needed

04:30-06:00  SENTENCE 3 — Build-up

06:00-07:30  SENTENCE 4 — Build-up

07:30-08:30  REPLAY 1-4 — Growing narrative in Arabic
             All four sentences, natural pacing, no English

08:30-09:00  English recap: "So far: the teacher walked to the cafe,
             but found a bookstore. He went inside..."

09:00-10:30  SENTENCE 5 — Build-up (story develops)

10:30-12:00  SENTENCE 6 — Build-up

12:00-12:30  REPLAY 5-6

12:30-14:00  SENTENCE 7 — Build-up

14:00-15:30  SENTENCE 8 — Build-up (story climax)

15:30-16:30  REPLAY 5-8 — Second half of story in Arabic

16:30-17:00  English recap of full story so far

17:00-19:00  SENTENCES 9-10 — Build-up (resolution)

19:00-20:00  REPLAY 9-10

20:00-23:00  FULL STORY — Arabic Only
             All 10 sentences played continuously, natural speed.
             No interruptions. Pure listening experience.
             (~15s per sentence × 10 = 2.5 min)

23:00-26:00  FULL STORY — Bilingual Version
             [Arabic sentence 1] → [1.5s] → [English sentence 1]
             [Arabic sentence 2] → [1.5s] → [English sentence 2]
             ...through all 10

26:00-28:00  VOCABULARY SPOTLIGHT
             "Three new words from today's story:"
             [Arabic word alone] → [English meaning] → [Root family]
             "كِتاب — book. From the root ك-ت-ب meaning writing.
              You already know كاتِب — writer."
             [Sentence from story containing the word, Arabic only]

28:00-29:00  FULL STORY — Arabic Only (final replay)
             Faster pacing. No pauses between sentences.

29:00-30:00  Closing: story title in Arabic, preview of next episode
```

### Arabic/English Mix
- Build-up phase: English first → Arabic chunks → Full Arabic (scaffolded reveal)
- Replay phases: Arabic only (growing confidence)
- Bilingual replay: Arabic → English (comprehension check)
- Final replay: Arabic only (consolidation)

### Repetition Pattern
Each sentence is heard **6-7 times**:
1. Clause-by-clause Arabic (build-up) — first exposure
2. Full sentence slow (build-up) — immediate
3. Full sentence normal (build-up) — immediate
4. Block replay (4 sentences) — 3-6 minutes later
5. Full story Arabic — 15-20 minutes later
6. Full story bilingual — 18-23 minutes later
7. Final full story — 25-28 minutes later

### Vocabulary Introduction
- Story generated with 5-7 new target words woven into the narrative
- Each new word appears in only one sentence where it's the sole unknown
- Build-up phase breaks the sentence into digestible chunks
- Vocabulary spotlight at the end connects new words to root families the learner already knows
- Root/pattern connections leverage the existing `Root.enrichment_json` and `Lemma.wazn` data

### Pacing
- Build-up: methodical, ~90 seconds per sentence
- Block replay: natural, ~15s per sentence
- Full story replay: natural continuous flow
- English recap: conversational, brief
- Total: ~10 sentences per episode is the sweet spot

### Assessment

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Passive suitability | 8/10 | No interaction. Slight cognitive demand during build-up is good. |
| Memory effectiveness | 9/10 | Narrative context creates rich encoding. Multiple retrieval passes. |
| Engagement | 8/10 | Story creates genuine curiosity. Climax/resolution structure maintains interest. |
| Difficulty calibration | 7/10 | Story generation constrains vocabulary, but narrative needs may introduce harder constructions. |
| Production complexity | 8/10 | Needs story generation (Opus), clause splitting, careful TTS pacing, English narration. |

### Pros
- Narrative context creates much stronger memory traces than isolated sentences
- The "growing replay" technique builds confidence — each time you hear the sequence, you understand more
- Root/pattern spotlight connects new words to existing knowledge (Arabic's morphological system is ideal for this)
- Three full-story replays mean the final listen should be near-100% comprehension — a powerful feeling
- Story can be logged as completed in the system, earning credit for all contained words
- Reuses the existing story generation pipeline (Opus + vocabulary constraints)

### Cons
- Story generation quality is variable — a bad story ruins the episode
- 10 sentences limits vocabulary coverage per episode
- If the story requires a word the learner doesn't know and it's not a target word, comprehension suffers
- Build-up phase can feel slow for sentences with all-known words
- More complex audio production pipeline (clause splitting, multiple speed passes)

---

## Format 3: Progressive Dialogue

### Concept
Two characters have a conversation that builds naturally. A narrator (in English) provides context and cultural notes. The conversation grows incrementally, with periodic "from the top" replays. Mimics eavesdropping on a real conversation with a helpful guide.

### Minute-by-Minute Structure (30 min)

```
00:00-00:45  SETUP (English narrator)
             "Layla and Kareem are at a restaurant. Layla is trying to
              order, but the waiter keeps suggesting different things..."

00:45-02:00  EXCHANGE 1 (2 lines)
             Narrator: "The waiter approaches. He says:"
             [Arabic - waiter line, slow] → [1s] → [English] → [1s] → [Arabic, normal]
             Narrator: "Layla responds:"
             [Arabic - Layla line, slow] → [1s] → [English] → [1s] → [Arabic, normal]

02:00-02:30  EXCHANGE 1 REPLAY — Both lines Arabic only, natural

02:30-04:00  EXCHANGE 2 (2 lines, same pattern)
             Narrator provides minimal bridging context

04:00-05:00  CONVERSATION SO FAR — Exchanges 1+2, Arabic only (4 lines)

05:00-06:30  EXCHANGE 3 (2 lines) — complication introduced

06:30-07:00  CULTURAL/LANGUAGE NOTE (English)
             "Notice how Kareem used the word أُرِيدُ — I want.
              The root ر-و-د has to do with wanting or seeking.
              You've seen this root in إرادة — will, determination."

07:00-08:30  EXCHANGE 4 (2 lines)

08:30-10:00  CONVERSATION SO FAR — All 8 lines, Arabic only
             Natural conversational pacing with brief pauses

10:00-11:30  EXCHANGE 5 (2 lines) — tension/humor peak

11:30-12:00  EXCHANGE 5 REPLAY

12:00-13:30  EXCHANGE 6 (2 lines) — resolution begins

13:30-14:00  NARRATOR RECAP (English)
             Brief summary of what happened so far

14:00-15:30  EXCHANGE 7 (2 lines) — resolution

15:30-16:00  EXCHANGE 7 REPLAY

16:00-18:00  FULL CONVERSATION — All 14 lines, Arabic only
             Two different voices (if TTS supports), natural speed

18:00-20:00  FULL CONVERSATION — Bilingual
             [Arabic line] → [1.5s] → [English line] for each

20:00-22:00  VOCABULARY DEEP DIVE (English + Arabic)
             Focus on 3-4 new words from the conversation
             Per word: meaning, root family, example from conversation,
             one additional example sentence

22:00-24:00  ROLE PLAY SECTION
             Narrator: "Now let's practice Layla's lines. I'll play
              Kareem, and there'll be a pause for you to recall
              Layla's response."
             [Kareem's line in Arabic] → [5s pause] → [Layla's line in Arabic]
             For 4-5 key exchanges

24:00-26:00  FULL CONVERSATION — Arabic only, slightly faster than natural
             The "test" — can you follow it all?

26:00-28:00  VARIATION ROUND
             Same conversation but with slight changes:
             "What if Layla ordered something different?"
             3-4 key sentences with one word substituted
             Tests whether learner has internalized the pattern vs. memorized the audio

28:00-30:00  CLOSING
             New vocabulary summary, preview of next conversation
             (Next episode: Layla and Kareem at the bookstore)
```

### Arabic/English Mix
- Introduction phase: Arabic sandwich (slow → English → normal)
- Replay phases: Arabic only
- Cultural notes: English with Arabic key terms
- Role play: Arabic with pause prompts
- Variation: Arabic with English setup

### Repetition Pattern
Each dialogue line heard **5-6 times**:
1. Introduction (slow + normal) — first exposure
2. Exchange replay — 30 seconds later
3. Cumulative replay — 3-8 minutes later
4. Full conversation Arabic — 15-18 minutes later
5. Role play (selected lines) — 20-22 minutes later
6. Full conversation final — 24-26 minutes later

### Vocabulary Introduction
- 5-6 new words woven into dialogue naturally
- Narrator explicitly glosses new words on first appearance
- Root/pattern connections in the cultural note section
- Variation round tests productive knowledge of new patterns

### Assessment

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Passive suitability | 7/10 | Role play section asks for mental recall — good for retention but requires focus. |
| Memory effectiveness | 9/10 | Dialogue is highly memorable. Role play forces active retrieval. Variations test transfer. |
| Engagement | 9/10 | Characters create investment. Humor/conflict sustain attention. |
| Difficulty calibration | 6/10 | Dialogue constraints are harder to control than monologue. Natural conversation may need words outside the known set. |
| Production complexity | 9/10 | Needs two Arabic TTS voices, English narrator, dialogue generation, variation generation. |

### Pros
- Most engaging format — characters and situations create emotional investment
- Role play section is the only format that prompts active recall (even passively, the pause creates prediction)
- Variation round tests real understanding vs. audio memorization
- Cultural notes feel natural in dialogue context
- Recurring characters across episodes create a serial — "what happens next?"
- Conversational patterns are immediately useful

### Cons
- Hardest to generate — natural dialogue is difficult for LLMs
- Two Arabic TTS voices needed (ElevenLabs supports this but doubles cost)
- Dialogue may require pragmatic vocabulary (greetings, hedges) that isn't in the learner's current SRS queue
- Role play section may be awkward while walking (people talking to themselves)
- Dialogue density: 14 lines = ~7 exchanges. Feels thin for 30 minutes without padding

---

## Format 4: Thematic Vocabulary Builder

### Concept
Each episode explores a root family or semantic theme, using Arabic's morphological system as the organizing principle. Leverages the existing `Root`, `Lemma.wazn`, and `Root.enrichment_json` data. The format moves from individual words to sentences to a mini-passage.

### Minute-by-Minute Structure (30 min)

```
00:00-01:00  THEME INTRODUCTION (English)
             "Today we're exploring the root ك-ت-ب — kaf, ta, ba.
              The core meaning: writing, inscription, the written word.
              You already know كِتاب — book. Let's discover the family."

01:00-04:00  ROOT TREE EXPLORATION
             For each word (4-6 words from the root):
               [Arabic word, slow] → "meaning [English]" → [Arabic again]
               Brief English note connecting to root meaning

             Example flow:
             "كِتاب — kitaab — book. The thing that is written."
             "كاتِب — kaatib — writer. The one who writes. Notice the
              faa'il pattern — the doer pattern. You know this from
              عالِم — a scholar, the one who knows."
             "مَكتَبة — maktaba — library. The place of writing.
              The ma-...-a pattern means a place — like مَدرَسة, school,
              the place of studying."
             "كَتَبَ — kataba — he wrote. The base verb."
             "مَكتوب — maktoob — written, a letter. The maf'uul pattern —
              the thing that was done to. Like مَعروف — known."

04:00-06:00  PATTERN SPOTLIGHT
             "Let's pause on that faa'il pattern — the doer pattern.
              You've seen it in:"
             [كاتِب] → writer → [عالِم] → scholar → [طالِب] → student
             "When you meet a new faa'il word, you can often guess:
              it's the person who does the action of the root."

06:00-07:00  PREDICTION GAME
             "Here's a root you know: ع-ل-م — knowing, knowledge.
              What would عالِم mean? ... [3s pause] ...
              A scholar — the one who knows."
             "Now try: the root د-ر-س means studying.
              What would دارِس mean? ... [3s pause] ...
              A student, one who studies."

07:00-12:00  SENTENCES — Known Words from Root
             5 sentences using root words the learner already knows
             Per sentence:
               [Arabic slow] → [1.5s] → [English] → [1s] → [Arabic normal]
             These are review — all words should be well-known

12:00-17:00  SENTENCES — New Words from Root
             5 sentences introducing 2-3 new words from the root family
             Per sentence:
               [English first] → [1.5s] → [Arabic slow] → [1.5s] →
               "The new word here is [Arabic word] — [English meaning]" →
               [Full Arabic sentence, normal speed]

17:00-19:00  SENTENCE RECALL — All 10 sentences
             [English] → [3s pause] → [Arabic]
             Rapid fire, testing retention

19:00-22:00  MINI-PASSAGE
             A 6-8 sentence passage using all the root's words in context
             First: English summary (30s)
             Then: Full Arabic passage, slow (90s)
             Then: Full Arabic passage, normal (60s)

22:00-24:00  PASSAGE — Bilingual
             [Arabic sentence] → [English] for each sentence

24:00-26:00  PASSAGE — Arabic Only (final)

26:00-28:00  CROSS-ROOT CONNECTIONS
             "The root ك-ت-ب and the root ق-ر-أ (reading) often appear
              together. Listen for both roots in these sentences:"
             3 sentences mixing both root families

28:00-30:00  CLOSING SUMMARY
             All new words listed: Arabic → English → root connection
             "Next episode: the root ع-م-ل — doing, making, working"
```

### Arabic/English Mix
- Root exploration: heavy English explanation with Arabic key terms
- Sentences: Arabic sandwich format
- Passage: Arabic-dominant with English scaffolding
- Cross-root: Arabic sentences with English framing

### Repetition Pattern
New words heard **6-7 times**:
1. Root tree (isolation) — minute 1-4
2. Pattern spotlight (if pattern-relevant) — minute 4-6
3. Sentence introduction — minute 12-17
4. Sentence recall — minute 17-19
5. Passage (slow) — minute 19-20
6. Passage (normal) — minute 20-21
7. Passage (bilingual) — minute 22-24

### Vocabulary Introduction
- 2-4 new words per episode, all from the same root family
- Root meaning taught first, then derivatives are predictable
- Pattern connections leverage existing knowledge (faa'il, maf'uul, etc.)
- Prediction game builds morphological awareness — the real superpower for Arabic

### Assessment

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Passive suitability | 8/10 | Prediction pauses are the only "active" part, and they work internally. |
| Memory effectiveness | 8/10 | Morphological connections create web-like encoding. Prediction enhances retention. |
| Engagement | 7/10 | Intellectually satisfying. "Aha moments" from pattern recognition. Can feel academic. |
| Difficulty calibration | 8/10 | Root families provide natural difficulty gradients. System knows which roots are partially known. |
| Production complexity | 7/10 | Needs root/pattern data (already exists), passage generation, English explanation scripts. |

### Pros
- Uniquely suited to Arabic — the root/pattern system is THE accelerator for vocabulary acquisition
- Leverages existing root enrichment data, pattern info, and wazn system
- Prediction game is the highest-retention technique that works passively
- 2-4 new words per episode means each gets deep treatment
- Builds morphological awareness that transfers to ALL future vocabulary
- "You already know X from the same root" creates network effects

### Cons
- **Semantic clustering warning**: Tinkham (1993/97) found that teaching semantically related words together can IMPEDE learning. Root families are morphologically related, not always semantically related (which is better), but some roots are tightly semantic (ك-ت-ب = all about writing). Monitor for interference.
- Can feel like a lecture rather than immersive experience
- Limited to 2-4 new words per episode — slower vocabulary growth
- Not all roots are equally productive — some episodes may feel thin
- Passage generation needs to naturally use many forms of one root (unusual)

---

## Format 5: Comprehensible Input Stream

### Concept
Maximizes Arabic exposure time. Mostly Arabic with surgical English insertions to gloss unknown words in real-time. Inspired by the "narrow listening" approach — repeated listenings of passages just above current level. The English acts like real-time subtitles for an Arabic audio experience.

### Minute-by-Minute Structure (30 min)

```
00:00-00:30  Brief English: "Today's stream. Mostly Arabic. I'll translate
             only the words you don't know yet."

00:30-05:00  PASSAGE 1 — Narrative (6-8 sentences, ~95% known)
             Inline glossing pattern:
             "أحمد ذَهَبَ إلى السوق — [Arabic flows naturally, then:]
              السوق — the market — [continues Arabic]
              وَاشتَرى — and he bought — خُبزاً — bread —
              واشترى خبزاً وحليباً"

             Only unknown/new words get English glosses.
             Known words flow in pure Arabic.
             After each sentence completes:
             [Full sentence replayed in Arabic, no interruption]

05:00-06:00  PASSAGE 1 REPLAY — Full Arabic, no glosses
             All 6-8 sentences continuously

06:00-06:30  Brief English bridge: "Ahmad went shopping. Now listen to
             what happened at the market."

06:30-11:00  PASSAGE 2 — Continuation (6-8 sentences, introduces 2-3 new words)
             Same inline glossing for unknown words only
             After each sentence: full Arabic replay

11:00-12:00  PASSAGE 2 REPLAY — Full Arabic, no glosses

12:00-12:30  English bridge: "Something unexpected happened..."

12:30-17:00  PASSAGE 3 — Climax/development (6-8 sentences)
             By now, words glossed in Passage 1 should be recognizable
             If repeated, they appear WITHOUT gloss (tests retention)

17:00-18:00  PASSAGE 3 REPLAY — Full Arabic

18:00-18:30  English bridge

18:30-22:00  PASSAGE 4 — Resolution (6-8 sentences)
             Near-zero glossing if earlier words were retained
             Maximum Arabic immersion

22:00-23:00  PASSAGE 4 REPLAY — Full Arabic

23:00-26:00  COMPLETE NARRATIVE — Full Arabic
             All ~28 sentences played as one continuous story
             Natural pacing, no pauses between passages
             This should feel like listening to a real Arabic story

26:00-28:30  COMPLETE NARRATIVE — With selective glosses
             Only the words introduced TODAY get glossed
             Everything else flows in Arabic

28:30-30:00  New word summary (English):
             "Five new words today: [word] — [meaning], ..."
```

### Arabic/English Mix
- Target: **80-85% of audio time is Arabic** (highest of any format)
- English appears only as: inline word glosses (2-3 words), brief bridges between passages, closing summary
- Glosses are whispered/reduced volume compared to Arabic flow (production trick)

### Repetition Pattern
Each sentence heard **4 times**:
1. With inline glosses — first exposure
2. Full sentence replay (immediate) — 10 seconds later
3. Passage replay — 3-5 minutes later
4. Complete narrative — 20-25 minutes later

New words heard **6-8 times** (appear in multiple sentences across passages):
- Glossed on first occurrence
- Un-glossed on second occurrence (within-episode test)
- Repeated in complete narrative replays

### Vocabulary Introduction
- 5-7 new words, each glossed inline on first occurrence
- Words deliberately repeated across passages (word appears in Passage 1 AND Passage 3)
- Second occurrence is un-glossed — forces the listener to recall from Passage 1
- If the same story continues across passages, repeated words feel natural

### Pacing
- Arabic: natural speed (1.0x) — this is immersion, not drilling
- Inline glosses: quick, matter-of-fact, low volume
- No long pauses — maintains the "stream" feeling
- Passage replays: slightly faster than first play
- Complete narrative: natural conversational speed

### Assessment

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Passive suitability | 10/10 | Most passive-friendly. Just listen. No pauses to fill, no prompts. |
| Memory effectiveness | 6/10 | High exposure time, but low retrieval demand. Incidental learning works but is slower. |
| Engagement | 7/10 | Immersive when calibrated right. Frustrating if too many unknowns. |
| Difficulty calibration | 8/10 | System can precisely control how many unknowns per sentence. |
| Production complexity | 8/10 | Inline glossing requires precise timing. Need to track which words need glosses. |

### Pros
- Maximum Arabic exposure time (~24 of 30 minutes is Arabic audio)
- Most natural listening experience — closest to real Arabic media consumption
- Inline glossing preserves the flow (unlike stop-and-explain methods)
- The "gloss → no-gloss" progression within the episode tests retention naturally
- Complete narrative replay at the end is deeply satisfying when comprehension clicks
- Lowest anxiety format — no expectations to perform, just absorb

### Cons
- Weakest for explicit vocabulary learning — incidental acquisition is slower
- If calibration is off (too many unknowns), the learner drowns
- No active retrieval practice — the brain encodes less deeply without testing
- Inline glossing is technically complex to produce (timing Arabic TTS + English inserts)
- Hard to log specific review credit — which words did the learner actually acquire?
- May create an illusion of understanding without true retention

---

## Format 6: Review & Reinforce

### Concept
Explicitly tied to the learner's current SRS state. Sentences are pulled directly from recent review sessions — the learner has already seen them on screen. The episode reinforces recent learning through the auditory channel, creating a multi-modal encoding advantage.

### Minute-by-Minute Structure (30 min)

```
00:00-01:00  Opening: "Review episode. Reinforcing words from your last
             three days of reviews. 22 sentences you've seen,
             6 that were tricky."

01:00-06:00  WARM-UP — 5 sentences, high-stability words (>30 days)
             [Arabic normal speed] → [2s] → [English] → [1.5s] → [Arabic]
             Fast-paced. These should be easy.
             "You know these well. Let them wash over you."

06:00-10:00  YESTERDAY'S TRICKY SENTENCES — 4 sentences rated "partial" or "missed"
             Per sentence:
               "This one was tricky yesterday. Listen:"
               [Arabic slow] → [2s]
               "The word you missed was [Arabic word] — [English meaning]."
               [Arabic slow, word emphasized with pause] → [2s]
               [Full Arabic, normal speed]
             More time per sentence for the hard ones.

10:00-13:00  THIS WEEK'S NEW WORDS — 3 sentences with recently-graduated words
             Per sentence:
               [Arabic slow] → [2s] → [English] → [2s] → [Arabic normal]
               "The new word: [Arabic] — [English]. Root: [root meaning]."
               [Arabic sentence one more time]

13:00-16:00  ACQUISITION REINFORCEMENT — 4 sentences with box 1-2 acquiring words
             These are the most fragile words. Extra repetition.
             Per sentence:
               [English first] → [3s pause — "can you picture the Arabic?"] →
               [Arabic slow] → [1s] → [Arabic normal]
               [Just the target word, isolated] → [English]
               [Arabic sentence one final time]

16:00-18:00  CUMULATIVE REPLAY — All 16 sentences so far, Arabic only
             Rapid fire, 4-5 seconds between sentences

18:00-21:00  LISTENING CHALLENGE — 5 new sentences using this week's vocabulary
             Sentences the learner has NOT seen before (from pre-generated pool)
             [Arabic normal speed] → [4s — "what did you understand?"] → [English]
             Tests whether review is translating to listening comprehension

21:00-23:00  REPLAY CHALLENGE SENTENCES — Arabic only
             All 5 challenge sentences, continuous

23:00-26:00  SPACED RECALL — 8 sentences from earlier in episode
             [English] → [4s pause] → [Arabic]
             Mix of easy (warm-up), tricky (yesterday's misses), and challenge

26:00-28:00  FULL EPISODE STREAM — All Arabic, all 21 sentences, natural pacing

28:00-29:30  PROGRESS NOTE (English)
             "This week: 4 new words graduated to long-term memory.
              3 tricky words are getting stronger. Your strongest root
              family is ك-ت-ب with 6 words known."

29:30-30:00  Closing + tomorrow's preview
```

### Arabic/English Mix
- Warm-up: balanced (sandwich)
- Tricky sentences: more English explanation
- Acquisition: English → pause → Arabic (recall direction)
- Challenge: Arabic → English (comprehension direction)
- Final stream: Arabic only

### Repetition Pattern
Tricky/acquiring words heard **6-7 times**:
1. Section introduction — minutes 6-13
2. Isolated word mention — same section
3. Cumulative replay — minute 16-18
4. Spaced recall — minute 23-26
5. Full stream — minute 26-28

Easy/warm-up words heard **3-4 times**:
1. Warm-up — minute 1-6
2. Cumulative replay — minute 16-18
3. Full stream — minute 26-28

### Vocabulary Introduction
- **No truly new words** — all words come from the SRS system
- But the "challenge" section uses **unseen sentences** with known words — this is a form of generalization testing
- Progress notes provide meta-awareness of learning trajectory

### Assessment

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Passive suitability | 9/10 | Some mental recall during pauses, but no phone needed. |
| Memory effectiveness | 8/10 | Multi-modal reinforcement (visual review + audio) is well-supported by research. |
| Engagement | 5/10 | Review is inherently less exciting than new content. Progress notes help. |
| Difficulty calibration | 10/10 | Directly mirrors SRS state. Every sentence is pre-scored. |
| Production complexity | 5/10 | Simplest to produce — sentences already exist with audio. Just needs sequencing. |

### Pros
- Most directly useful for retention — reinforces exactly what the learner studied
- Multi-modal encoding (saw it on screen yesterday, hearing it today) is a proven accelerator
- Challenge section tests generalization — can you understand known words in NEW contexts?
- Easiest to produce: sentences, audio, and translations already exist in the DB
- FSRS review credit is natural — each sentence heard maps to existing review records
- Progress notes create motivation and meta-cognitive awareness

### Cons
- No new vocabulary growth — this episode purely reinforces
- Hearing sentences you just reviewed on screen can feel redundant
- Limited engagement — no story, no characters, no surprise
- May feel like homework rather than walking entertainment
- If the learner had a good review session (all correct), the "tricky" section has nothing interesting

---

## Format 7: Hybrid/Layered (Recommended Primary Format)

### Concept
Combines the strongest elements of all formats into a layered structure. Opens with familiar warmth, teaches through story, drills through practice, closes with immersion. Each section serves a distinct cognitive function.

### Minute-by-Minute Structure (30 min)

```
00:00-00:30  THEME MUSIC + Brief English intro
             "Episode 23. Today: a story about a lost letter,
              plus 5 review sentences and 3 words from the root ك-ت-ب."

── LAYER 1: WARM-UP (2 min) ──────────────────────────
00:30-02:30  5 HIGH-STABILITY SENTENCES — Arabic only, natural speed
             [Sentence] → [3s] → [Sentence] → [3s] → ...
             No English. These are words with >30 day FSRS stability.
             Purpose: tune the ear to Arabic phonology, build confidence.
             Brief English after: "You understood all of that. Good."

── LAYER 2: VOCABULARY PREVIEW (3 min) ────────────────
02:30-05:30  NEW WORD INTRODUCTION — 3-5 new words
             Per word (30-45s):
               [Arabic word, slow] → [English meaning]
               [Root connection]: "From root X, you know Y and Z"
               [Pattern connection]: "The faa'il pattern, like [known word]"
               [Arabic word in a short phrase]

             Example:
             "مَكتوب — maktoob — written, or a letter.
              Root ك-ت-ب — writing. You know كِتاب — book, and كاتِب — writer.
              مَكتوب uses the maf'uul pattern — the thing done.
              Like مَعروف — known, from عَرَفَ — to know.
              مَكتوب قَديم — an old letter."

── LAYER 3: STORY (15 min) ────────────────────────────
05:30-06:30  STORY PREMISE (English)
             "Maryam found an old letter — مَكتوب — in her grandmother's
              house. The letter was written in beautiful handwriting..."

06:30-08:00  SENTENCES 1-2 — Build-up
             [English sentence] → [Arabic slow] → [Arabic normal]
             New words get a brief inline gloss on first appearance

08:00-09:30  SENTENCES 3-4 — Build-up

09:30-10:00  REPLAY 1-4 — Arabic only

10:00-11:30  SENTENCES 5-6 — Build-up (story develops)

11:30-13:00  SENTENCES 7-8 — Build-up (climax)

13:00-13:30  REPLAY 5-8 — Arabic only

13:30-14:30  SENTENCES 9-10 — Resolution

14:30-15:00  REPLAY 9-10

15:00-16:30  FULL STORY — Arabic only, natural speed
             All 10 sentences. Should feel like a complete listen.

16:30-18:00  FULL STORY — Bilingual
             [Arabic] → [1.5s] → [English] for each sentence

18:00-18:30  Brief English: "Beautiful. Let's practice the key patterns."

── LAYER 4: DRILL (6 min) ─────────────────────────────
18:30-20:30  SENTENCE DRILL — 5 sentences mixing story + review vocabulary
             [Arabic slow] → [2s] → [English] → [1.5s] → [Arabic normal]

             3 sentences from SRS (words due for review)
             2 sentences using new words from today's episode
             All sentences are fresh — not from the story itself

20:30-22:30  RECALL DRILL — Same 5 + 3 from story
             [English] → [4s pause] → [Arabic]

22:30-24:00  RAPID ARABIC — All 8 drill sentences, Arabic only, quick pacing

── LAYER 5: IMMERSION CLOSE (6 min) ───────────────────
24:00-26:30  FULL STORY — Final Arabic replay
             Slightly faster than earlier. No pauses between sentences.
             "Just listen and enjoy."

26:30-28:00  VOCABULARY RECAP
             Each new word: [Arabic] → [English] → [Root] → [Example from story]

28:00-29:30  PREVIEW
             "Tomorrow: we explore what happened to Maryam's grandmother.
              And three new words from the root ق-ر-أ — reading.
              Here's a taste: [one Arabic sentence from next episode]"

29:30-30:00  THEME MUSIC OUT
```

### Layer Design Rationale

| Layer | Duration | Cognitive Function | Format Source |
|-------|----------|-------------------|---------------|
| Warm-up | 2 min | Phonological priming, confidence | Format 6 |
| Vocab Preview | 3 min | Schema activation, root/pattern anchoring | Format 4 |
| Story | 15 min | Deep encoding via narrative context | Format 2 |
| Drill | 6 min | Active retrieval, SRS integration | Format 1 |
| Immersion Close | 4 min | Consolidation, full comprehension confidence | Format 5 |

### Arabic/English Mix by Layer
- Warm-up: 95% Arabic
- Vocab Preview: 60% English / 40% Arabic (teaching mode)
- Story: 65% Arabic / 35% English (declining English as story progresses)
- Drill: 50/50
- Immersion Close: 90% Arabic

**Overall episode**: approximately **65% Arabic, 35% English**

### Repetition Pattern
New words heard **8-10 times across the episode**:
1. Vocab preview (isolated + phrase) — minute 2-5
2. Story first appearance (with gloss) — minute 6-14
3. Story replay (Arabic only) — minute 15-16
4. Story replay (bilingual) — minute 16-18
5. Drill (in new sentence) — minute 18-22
6. Recall drill — minute 20-22
7. Rapid Arabic drill — minute 22-24
8. Final story replay — minute 24-26
9. Vocab recap — minute 26-28

**Pimsleur-aligned spacing**: introduced at minute ~3, recalled at minute ~5 (story), ~10 (story block replay), ~20 (drill), ~25 (final replay). This maps roughly to the 2min, 10min, 25min intervals.

Review words heard **4-5 times**:
1. Warm-up — minute 0-2
2. Drill — minute 18-22
3. Recall — minute 20-22
4. Rapid Arabic — minute 22-24

### Vocabulary Introduction
- 3-5 new words per episode (conservative — quality over quantity)
- Each word pre-taught with root/pattern connections BEFORE the story (schema activation)
- Story provides narrative context for encoding
- Drill provides retrieval practice in novel sentences
- Words appear across multiple layers, each serving a different memory function

### Assessment

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Passive suitability | 9/10 | Varied pace keeps attention. Recall pauses are internal. |
| Memory effectiveness | 9/10 | Multiple encoding types (explicit, narrative, retrieval, immersion). Best coverage. |
| Engagement | 8/10 | Story keeps interest. Variety prevents monotony. Sections change mood. |
| Difficulty calibration | 8/10 | Uses SRS data for warm-up + drill. Story constrains vocabulary. |
| Production complexity | 9/10 | Most components to orchestrate. But each is well-understood. |

### Pros
- Every cognitive mechanism represented: schema activation, narrative encoding, active retrieval, immersion
- The transition from "I don't know these words" (vocab preview) to "I understand a whole story" (final replay) is deeply motivating
- Root/pattern teaching builds systemic knowledge unique to Arabic
- SRS review words get credit in the drill section
- Serial story arc ("tomorrow: what happened to Maryam's grandmother") creates habit-forming anticipation
- Each layer can be tuned independently — make drill longer if many words due, make vocab preview shorter if introducing only 1 new word
- The 15-minute story core is substantial enough to feel like real Arabic consumption

### Cons
- Most complex to produce — 5 distinct sections with different generation requirements
- 30 minutes is tight for all 5 layers — some episodes may need to drop the drill or shorten the warm-up
- Transitions between layers can feel jarring if not handled smoothly
- Story generation + drill generation + vocab explanation generation = 3 distinct LLM tasks
- Risk of trying to do too much — could feel scattered rather than focused

---

## Additional Format: Format 8 — Shadowing Stream

### Concept
A pure pronunciation/listening format where sentences are played with strategic pauses for the learner to mentally shadow (or whisper) the Arabic. Even if the learner doesn't speak aloud, the mental rehearsal of phonological forms strengthens the audio-form memory trace. Not for vocabulary learning — for cementing the sound of words already being learned.

### Structure (abbreviated)
```
Per sentence (30-40s):
  [Arabic normal] → [English] → [Arabic slow, word-by-word with 0.5s gaps] →
  [3s pause — shadow the sentence] → [Arabic normal]

30 sentences in 25 minutes + 5 min warm-up/cool-down
```

### Assessment
| Criterion | Rating |
|-----------|--------|
| Passive suitability | 6/10 — ideally wants vocalization |
| Memory effectiveness | 7/10 — phonological loop rehearsal |
| Engagement | 3/10 — extremely repetitive |
| Difficulty calibration | 9/10 — direct from SRS |
| Production complexity | 4/10 — simple sequencing |

Best used as an **occasional supplement**, not primary format.

---

## Comparative Analysis

### Overall Rankings

| Format | Passive | Memory | Engagement | Calibration | Production | **Weighted Total** |
|--------|---------|--------|------------|-------------|------------|-------------------|
| F7: Hybrid | 9 | 9 | 8 | 8 | 9 | **8.5** |
| F2: Story | 8 | 9 | 8 | 7 | 8 | **8.0** |
| F3: Dialogue | 7 | 9 | 9 | 6 | 9 | **7.8** |
| F4: Thematic | 8 | 8 | 7 | 8 | 7 | **7.7** |
| F1: Drill | 9 | 7 | 4 | 9 | 6 | **7.0** |
| F6: Review | 9 | 8 | 5 | 10 | 5 | **7.4** |
| F5: CI Stream | 10 | 6 | 7 | 8 | 8 | **7.4** |
| F8: Shadow | 6 | 7 | 3 | 9 | 4 | **5.8** |

(Weights: passive 15%, memory 30%, engagement 25%, calibration 15%, production 15%)

### Recommended Episode Rotation

For a **daily 30-minute walk**:

| Day | Format | Rationale |
|-----|--------|-----------|
| Mon | **F7: Hybrid** | Full learning episode — new story, new words, review drill |
| Tue | **F6: Review** | Reinforce Monday's new words + clear SRS backlog |
| Wed | **F7: Hybrid** | New story continuing Monday's narrative arc, new words |
| Thu | **F4: Thematic** | Deep dive into a root family — systematic knowledge building |
| Fri | **F5: CI Stream** | Pure immersion — replay the week's stories + new passage |
| Sat | **F3: Dialogue** | Weekend treat — engaging characters, lighter cognitive load |
| Sun | **F6: Review** | Week recap — all new words from Mon-Sat, progress summary |

This gives: **2 hybrid** (core learning) + **2 review** (reinforcement) + **1 thematic** (systematic) + **1 immersion** (confidence) + **1 dialogue** (engagement) per week.

**Expected weekly intake**: 6-10 new words (Mon + Wed hybrid episodes), reinforced across 5 other formats.

---

## Key Design Parameters (Applicable to All Formats)

### TTS Configuration
- **Arabic TTS**: ElevenLabs, `eleven_multilingual_v2`, Chaouki voice (already configured)
  - Slow: speed 0.7x (existing setting)
  - Normal: speed 1.0x (need new setting)
  - Fast: speed 1.15x (for final replays)
- **English TTS**: Needs a separate voice. Options:
  - ElevenLabs English voice (cost concern)
  - System TTS (free, lower quality)
  - Pre-recorded narrator templates with variable insertions
- **Arabic word isolation**: For vocabulary previews, generate individual word audio
- **Silence insertion**: 1-5 second gaps via programmatic padding

### Sentence Selection Criteria for Podcast
Tighter than reading mode:
- **Comprehensibility**: >= 90% known scaffold words (vs 60% for reading)
- **Max sentence length**: 10 words (listening constraint from `listening.py`)
- **Stability floor**: scaffold words need `MIN_LISTENING_STABILITY_DAYS = 7.0`
- **One unknown per sentence maximum** for new vocabulary introduction
- **Diverse sentence pool**: no sentence repeated across episodes within 7 days

### SRS Credit Model
How does listening to a podcast sentence earn FSRS credit?

**Option A: Full credit** — each sentence heard = a review with Rating 3 (Good). Problem: no verification the learner actually understood.

**Option B: No credit** — podcast is pure supplementary exposure. Reviews only happen in-app. Problem: misses the multi-modal encoding benefit.

**Option C: Partial credit (recommended)** — podcast exposure logged as `review_mode="podcast"`, weighted at 0.5x a normal review. FSRS gets the signal but with lower confidence. Words that only have podcast reviews don't graduate — they still need in-app confirmation.

**Option D: Encounter credit** — podcast exposure increments `total_encounters` and `distinct_contexts` but doesn't count as a formal review. This is the safest option and most honest about what passive listening achieves.

### Episode Generation Pipeline

```
1. Query SRS state → identify due words, acquiring words, high-stability words
2. Select format based on schedule (or user preference)
3. Generate story/dialogue/passage via LLM (Opus for stories, Sonnet for everything else)
4. Validate all sentences through existing pipeline (map_tokens, verify_mappings)
5. Generate Arabic TTS for all sentences (slow + normal + fast variants)
6. Generate English TTS for translations + narration
7. Assemble audio segments with silence padding
8. Encode as single MP3 with chapter markers
9. Log episode contents for SRS credit tracking
```

### Cost Estimation (per episode)

| Component | ElevenLabs Characters | Cost @ $0.30/1K chars |
|-----------|----------------------|----------------------|
| Arabic sentences (10 sentences × 3 speeds) | ~2,100 | $0.63 |
| Arabic words (isolation, 5 words × 2) | ~100 | $0.03 |
| English translations + narration | ~3,000 | $0.90 |
| **Total per episode** | **~5,200** | **$1.56** |
| **Monthly (daily episodes)** | **~156,000** | **$46.80** |

**Cost reduction strategies**:
- Cache Arabic sentence audio (already implemented — reuse across episodes)
- Use system TTS for English narration (free)
- Generate English narrator segments once as templates ("This one was tricky yesterday", "The root means...", etc.)
- Share Arabic TTS across formats (a sentence used in Monday's hybrid is cached for Tuesday's review)

With English system TTS: **~$0.66/episode, ~$20/month**.

---

## Implementation Phases

**Phase 1 — Proof of Concept**: Format 1 (Drill). Simplest to build. Validates the audio assembly pipeline. Uses only existing sentences + TTS.

**Phase 2 — Story Integration**: Format 2 (Story Breakdown). Adds story generation + clause splitting + build-up pedagogy.

**Phase 3 — Full Hybrid**: Format 7. Combines all layers. Requires the complete pipeline.

**Phase 4 — Rotation System**: Multiple formats with scheduling. Episode recommendation based on SRS state + day of week.
