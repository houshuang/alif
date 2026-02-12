# Learner Profile — Stian

> Date: 2026-02-12
> Source: Structured interview, Substack article, observed behavior

---

## Identity & Motivation

- **True beginner** in Arabic (<6 months), but NOT a beginner at learning. Has prior experience building sentence-based language learning tools (Glossarium) and studying other languages.
- **Builder-learner**: Literally constructing his own learning tool. Wants the algorithm to be *right*, not just "good enough." Will read the research and question decisions.
- **Patient and self-honest**: No gamification needed. No need for quizzes to verify honesty — "I'm 100% honest, so I don't need any more obstacles like Duolingo quiz." Will show up consistently if the system works.
- **Research-driven**: Makes decisions based on evidence, not intuition or marketing. Reads papers. Questions whether the algorithm is optimal.
- **High agency, high motivation**: Willing to do a LOT if he trusts the process. The key is *trust* — which is earned by visible progress.

## The Core Motivation: "Read the Last Page"

The deepest motivational pattern: **uploading a text he cares about (poem, song, story) → doing targeted practice → coming back a week later and reading it fluently**. This is the "holy grail" moment. Not abstract progress bars, but the lived experience of comprehension.

> "That feeling of being able to read the story fluently and enjoy the beauty, no tricks, is the most amazing feeling and motivation I can have."

This maps directly to the Story mode flow: import → learn targeted vocab → read and complete. The algorithm's job is to make this loop reliable and fast.

## What "Progress" Means

NOT: "I reviewed 200 cards today"
NOT: "My streak is 14 days"
IS: **"The number of words I confidently know — that if given a new text, there's a high probability I'd recognize them — is significantly increasing week over week."**

He cares about *functional reading vocabulary growth* — words that are genuinely consolidated, not just "seen." The current system fails this because FSRS thinks 586 words are known when only ~150-200 actually are.

## Trust Breakers

1. **Failing the same words repeatedly** without the system doing something about it. Leeches that keep appearing feel like the system isn't learning.
2. **No visible progress** — doing hundreds of reviews but the "known word count" (real known, not FSRS-inflated) doesn't grow.
3. NOT a trust breaker: difficulty, struggle, or low session accuracy. He's patient with hard material if he believes it's building toward something.

## Michel Thomas Principle

Inspired by Michel Thomas' approach: **"The teacher manages the memory for you — if you do as I say and put effort into each individual interaction, trust me that you'll learn."**

This means: the algorithm should take FULL responsibility for scheduling, repetition, leech management, and pacing. The user's only job is to show up and engage honestly with each card. "Automate everything."

The worry with the textbook is: "I constantly worry that the things I learnt, I will forget." Alif should eliminate this worry completely — if it's in the system, it will be reviewed at the right time.

## Learning Style

- **Sentences >>> individual words**. Strong opinion based on prior experience (Glossarium). Sentences provide context anchoring, pattern recognition, multiple-word reinforcement, and feel like real reading.
- **Analytical**: Wants to understand roots, patterns, morphology — not just memorize. The root prediction feature and morphological display align with this.
- **Slow, thorough processing**: When facing a hard sentence (3-4 unknowns), works through it slowly using context, then flips to check. Doesn't give up or rush.
- **Honest self-assessment**: Triple-tap marking is reliable signal from this user. No inflation risk.

## Schedule Pattern

- **Variable**: Some days 5 min (phone pickup), some days 45 min (train ride). Cannot count on consistent session length.
- The algorithm must handle both micro-sessions (3-4 cards) and deep sessions (30+ cards) gracefully.
- **Front-loading priority items** is critical — if he only does 3 cards, those 3 must be the highest-impact items.

## Study Context

- Primary: Alif + one textbook (working through linearly)
- OCR captures textbook pages into Alif to reinforce what's being studied
- Future OCR imports will be incremental (a few pages at a time), not bulk
- Wants the textbook vocab to be tracked AND actively learned
- Story imports for motivational texts (poems, songs, articles)

## Goals (1-2 months)

- **Read a short Arabic paragraph and understand 80%+ without help**
- Reading is the clear priority. Listening is a future concern.
- Religious texts (Quran) are a stretch goal, not immediate.

## Algorithm Implications

1. **Metric that matters**: Growth rate of "genuinely known" words per week (real stability, not inflated FSRS)
2. **Story mode is the motivational engine**: Import → learn → read → feel the magic. Make this loop fast and reliable.
3. **Session design**: Must work for both 3-card and 30-card sessions. Always start with highest-impact items.
4. **No gamification needed**: Progress visualization yes, badges/streaks/points no.
5. **Full automation**: User trusts the algorithm. Don't ask for decisions — make them and show the reasoning.
6. **Leech management must be invisible**: Auto-suspend, auto-reintroduce, auto-escalate. Don't burden the user with "this word is a leech, what do you want to do?"
7. **Textbook sync**: Each OCR capture should seamlessly feed the review pipeline. New pages = new words entering acquisition phase.
8. **Sentences always**: Even during acquisition phase, use sentence context. Never show isolated word flashcards unless absolutely no sentences are available.
