# Alif — Learn to Read and Listen to Arabic

I've been building a personal Arabic comprehension trainer. It's input-only — just reading and listening, no production exercises. The idea is that comprehension comes from massive meaningful input, not from writing or translating. It's an iPhone app that works fully offline and syncs when you're back online.

The core: you review full Arabic sentences, not isolated flashcards, and every word in the sentence feeds into the spaced repetition algorithm (FSRS). So nothing you read is wasted — even supporting words get their review schedules updated. You mark words with three states — missed, confused, or understood — which captures the real gradient of partial knowledge and schedules accordingly.

The session builder uses a greedy set cover algorithm to pick sentences covering the maximum number of due words, then adjusts for sentence diversity (so you don't see the same context frame 50 times), comprehension-aware recency (struggled sentences come back in 2 days, understood ones wait 7), difficulty matching (new words surrounded by well-known scaffold), and grammar progression — it tracks 24 grammar features across 5 tiers and gradually increases sentence complexity as your comfort grows.

It's Arabic-specific: leverages 3-consonant root families (if you know "book" and "writer" from root K-T-B, it asks "can you guess?" when you see "library"), strips clitics automatically, shows full diacritization always, and has a listening mode with slowed TTS that only activates once words are mature enough in your memory.

Two features I think are pretty unique: you can photograph pages from a physical textbook you're studying from — it OCRs the Arabic, updates weights for words you've already seen, and adds new words. So you can study from a real book by the fireplace and review those exact words on your phone on the subway later.

The other piece is story mode. Most apps and textbooks teach you words but never give you enough reading at your level to build actual fluency. Alif generates stories using only words you know, so you always have something to read that's challenging but not frustrating. And there's a goal mode: upload a poem or article you actually want to read, the app finds the 30 words you're missing, schedules them as priority vocabulary, and if you just use the app normally for a few days you'll learn those words through example sentences. Come back at the end of the week and you can actually read the thing. For a serious learner that's a bigger unlock than any gamification.

No gamification, no multiple choice, no writing. Just reading and listening with good scheduling underneath.
