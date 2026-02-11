# Alif — Learn to Read and Listen to Arabic

A personal Arabic comprehension trainer. No writing exercises, no multiple choice, no gamification. You read and listen to Arabic sentences, mark words you didn't know, and the system schedules everything for optimal long-term retention.

## The Core Idea: Sentence-Centric Spaced Repetition

Most vocabulary apps show you isolated flashcards. Alif shows you full Arabic sentences.

When you review a sentence, **every word in that sentence gets scheduled for future review** — not just the target word. If you review a sentence because "school" (مَدْرَسَة) was due, the verb "went" (ذَهَبَ) and the preposition "to" (إِلَى) also get credit. This means your actual reading experience — processing words in context — directly feeds the spaced repetition algorithm. Nothing is wasted.

The scheduler uses FSRS (Free Spaced Repetition Scheduler), which models your personal forgetting curve per word. Words you struggle with come back quickly; words you know well wait longer.

## How a Review Session Works

### Reading Mode
1. You see a fully diacritized Arabic sentence (all vowel marks shown)
2. **Before revealing the answer**, you can tap any word to look it up — this shows the root, meaning, and related words you already know
3. You reveal the English translation
4. You mark words you missed (red) or found confusing (yellow) by tapping them
5. You rate your overall comprehension: understood / partial / no idea

### Listening Mode
1. Audio plays at 0.7x speed with learner-friendly pauses (a pause inserted every 2 words)
2. You try to understand without seeing the text
3. You reveal the Arabic, then the English, marking words you didn't catch
4. Same rating as reading mode

The listening mode has a readiness filter: a word only appears in listening exercises after you've reviewed it 3+ times and have 7+ days of retention stability. This prevents the frustration of trying to hear words you barely recognize visually.

## Intelligent Session Assembly

Sessions aren't random. A 6-stage pipeline selects which sentences to show you:

1. **Coverage optimization**: A greedy algorithm picks sentences that collectively cover the most due words — so each sentence does double or triple duty
2. **Comprehension-aware recency**: Sentences you understood wait 7 days before reappearing. Sentences you partially got come back in 2 days. Sentences you didn't understand at all return in 4 hours
3. **Difficulty matching**: If you're reviewing a new word, the surrounding words in the sentence should be well-known (so you can focus on the target). If you're reviewing a strong word, the context can be harder
4. **Sentence diversity**: Words that have appeared in too many recent sentences get deprioritized. This prevents "Muhammad went to the school" from becoming your only sentence frame
5. **Grammar fit**: Sentences with grammar features you haven't seen yet are deprioritized; sentences reinforcing features you're building comfort with are boosted
6. **Session pacing**: Easy sentences at the start and end (warm-up and cool-down), harder material in the middle

## Arabic-Specific Features

### Root-Based Learning
Arabic vocabulary is built from 3-consonant roots. The root K-T-B (ك ت ب) gives you كِتَاب (book), كَاتِب (writer), مَكْتَب (office), مَكْتَبَة (library). Alif leverages this:

- **Root prediction**: When you tap an unknown word during review, if you already know 2+ words from the same root, the app asks "Can you guess the meaning?" before showing the translation. This activates your morphological reasoning
- **Root-aware word selection**: When choosing which new words to teach you, the algorithm prefers words from roots where you know 30-60% of the family — the sweet spot for productive learning
- **Recency clustering**: If you learned one word from a root yesterday, related words from the same root get a boost today (momentum effect)

### Morphological Variant Handling
Arabic has extensive morphology — the same word appears with different prefixes, suffixes, and vowel patterns. The app uses CAMeL Tools (a computational Arabic morphology library) to:

- Detect that بِنْتِي (my daughter) is a variant of بِنْت (daughter), not a separate word
- Recognize that الكِتَاب (the book) and كِتَاب (book) share one spaced repetition card
- Track accuracy per variant form — if you consistently miss the feminine form of an adjective, that gets surfaced

### Clitic Stripping
Arabic attaches prepositions, conjunctions, and pronouns directly to words (وَبِمَدْرَسَتِهِمْ = "and in their school" is one written token). The validator strips these systematically to match words against your vocabulary.

### Full Diacritization
All Arabic text is shown with full vowel marks (tashkeel). This is unusual — most Arabic text is written without vowels, which makes it harder for learners. The app always generates diacritized text.

## Learning New Words (Learn Mode)

You control which words to learn. The app suggests 5 candidates at a time, selected by:
- 40% word frequency (common words first)
- 30% root familiarity (words from partially-known root families)
- 20% recency bonus (siblings of recently learned words)
- 10% grammar pattern coverage (filling gaps in your morphological exposure)

For each candidate you see: Arabic text, English meaning, transliteration, part of speech, conjugation/declension table, an example sentence, root family, and audio. You choose to learn, skip, or permanently dismiss each word.

After introducing words, you immediately quiz on them in sentence context.

## Bridging Physical and Digital Study

### Textbook Scanner
You can photograph pages from a physical textbook you're studying from. The app OCRs the Arabic text, reduces every word to its dictionary form via morphological analysis, and updates your vocabulary state — words you've already seen in the textbook get their review weights updated, and new words get added to your learning queue. This means you can sit down with a physical book, study a chapter, scan the pages, and then review exactly those words later on your phone during a commute. The physical and digital study reinforce each other instead of being separate tracks.

### Story Mode — Building Fluency Through Reading
This addresses a gap in most vocabulary apps and textbooks: once you've learned words, you need extended reading practice with those words to build actual fluency. Knowing a word in isolation and reading it fluently in context are different skills.

**Generated stories**: The app generates short Arabic stories (4-12 sentences) tailored to your vocabulary. An LLM writes the story using words you know, then a rule-based validator checks every word. Stories with too many unknown words get rejected and regenerated. You get reading material that's always at your level — not too easy, not frustrating.

**Reading flow**: Full-screen Arabic text with tap-to-look-up on every word. Toggle between Arabic and English views. When you finish, every word you didn't look up gets "understood" credit; words you tapped get marked as missed. Extensive reading directly feeds your spaced repetition data.

**Readiness indicators**: Stories show color-coded readiness — green (3 or fewer unknown words), orange, red — targeting 85-95% vocabulary coverage, the research-backed sweet spot where you can follow the story while still learning from context.

### Goal Mode — Read What You Actually Want to Read
You can upload a poem, short story, news article, or any Arabic text that you want to be able to read. The app analyzes it and tells you exactly where you stand — maybe you know 70 out of 100 words. Instead of starting to read and hitting a wall of unknown vocabulary, the app identifies the 30 missing words and schedules them as high-priority new vocabulary, teaching them through simple example sentences using the normal spaced repetition flow.

If you just use the app normally for a few days after that, you'll naturally learn and practice those target words. When you come back to the original text at the end of the week, you can actually read it fluently. For a serious language learner, having a concrete reading goal that becomes achievable through daily practice is a bigger motivator than any streak counter or point system.

## Grammar Progression

24 grammar features across categories (number, gender, verb tenses, verb forms I-X, syntax patterns) tracked through a 5-tier progression system. You don't study grammar explicitly — the app tracks which features you're encountering through your reading and listening, builds a comfort score per feature, and gradually introduces more complex grammar in generated sentences as your comfort grows.

## Ternary Word Marking

Most apps give you binary feedback: right or wrong. Alif has three states:
- **Missed** (red, FSRS rating "Again"): you didn't know the word
- **Confused** (yellow, FSRS rating "Hard"): you know it but got mixed up — common with similar-looking Arabic words or weak conjugation forms
- **Unmarked** (FSRS rating "Good"): you understood it

This distinction matters because FSRS schedules "Hard" differently from "Again" — a confused word comes back sooner than normal but not as aggressively as a fully missed word. It captures the real gradient of partial knowledge.

## Offline-First

All reviews queue locally and sync when you're back online. Sessions are cached (3 per mode). Story lookups persist in local storage. You can practice on a plane or subway without losing anything.

## What It Doesn't Do

- No writing or production exercises
- No gamification (no streaks, points, leaderboards)
- No social features
- No multiple choice quizzes
- No explicit grammar lessons
- Single user, no accounts

It's a tool for one person who wants to get better at reading and understanding Arabic, built around the idea that meaningful input — reading and listening to real sentences — is how comprehension develops, and that a scheduling algorithm should handle the tedious work of deciding what to review when.
