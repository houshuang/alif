# Walk audio, November 2026

A second attempt at hands-off Arabic audio for a daily 20–30 minute walk. The first attempt was the
podcast system of March–April 2026. This note records the data, the research and the design of the
pilot. The experiment-log entry is "2026-11-21 (2)".

## Learner data on 2026-11-21

| Measure | Value |
|---|---|
| Known words | 2,690 (plus 50 learning, 112 lapsed, 96 acquiring) |
| Firm words (FSRS stability ≥ 30 days) | 1,981 |
| Core frequency list, top 995 entries known | 943 |
| Core frequency list, top 1,992 entries known | 1,742 |
| Scheduled words never heard (`times_heard = 0`) | 2,786 of 2,841 |
| Listening-mode sentence reviews, all time | 14 (February 2026) |
| March–April podcasts | 19 episodes, 5–8 minutes; about 11 finished 22–28 March; April repetition episodes hardly opened |
| New audio generation | off since 2026-05-12 (`ALIF_AUDIO_ENABLED` unset) |

The print vocabulary is large enough for graded speech. Almost none of it has been heard. The March
episodes were written for an 800-word learner, with one voice and English around every Arabic sentence.

## Research

Web research on 2026-11-21. "(recalled)" marks a result cited from memory of the literature and not
re-read on that date.

- **Coverage.** Comprehension of spoken narratives was good at 90–95% known words, about 2,000–3,000
  word families (van Zeeland & Schmitt 2013).
- **New words from listening.** Listening-only gave the lowest gains of three modes, with 20–100
  encounters cited as needed (Brown, Waring & Donkaewbua 2008). Later work found similar gains across
  modes, about 13–17% (Feng & Webb 2020; Webb et al. 2023). Audio is used here to consolidate known
  words, not to introduce new ones.
- **Pauses and speed.** Inserted pauses improved comprehension where mechanical slowing did not (Blau
  1990; Van Loon 2000). Other studies do find slower rates help (Zhao 1997; Griffiths 1990, 1992).
- **Repetition.** Comprehension rose with each repeated listening for weak and strong listeners
  (Rodrigo 2017).
- **One topic over several days.** One small study supports narrow listening (Yến & Waring 2022,
  n = 38). Topic familiarity aids comprehension (Schmidt-Rinehart 1994, recalled).
- **Reading while listening.** The combined group gained most listening fluency over 13 weeks of graded
  readers (Chang & Millett 2014, result recalled).
- **Vocabulary by sound.** Learners know fewer words by sound than in print, and the sound-based
  measure predicts listening better. No study was found on learners of Arabic specifically.
- **Transfer to reading.** Not directly tested. To be measured in the app's own review data.
- **Case endings.** Sparse in spoken MSA (Hallberg 2016, 17 televised interviews). The episodes are
  fully vowelled in the script and voiced with pausal sentence endings.
- **Closest published design.** SRS-Stories (EMNLP 2025): a spaced-repetition engine picks words, an
  LLM writes the story, a lemmatising checker triggers rewrites. No audio; judged by an LLM.
- **Not found.** A measured speech rate for Arabic news; an independent comparison of Arabic speech
  engines on vowelled MSA; any write-up of personalised known-word audio succeeding or failing.

Speech engines (vendor pages, 2026-11-21): ElevenLabs about $0.08 per 1,000 characters, with cloned
voices; Gemini `gemini-3.8-flash-tts` about $0.40 per 30 minutes, two speakers in one request, stock
voices only; Azure and OpenAI single-speaker.

## Design

- **Vocabulary.** Firm words only, plus names. Up to 45 weak words (due or lapsed, seen at least three
  times) are offered for repetition; half of the weak words are held out as a comparison group.
- **Check.** Every token goes through the running-text mapper. A turn with a word outside the allowed
  set is rewritten, up to three rounds.
- **Format.** Two hosts. One tells the subject; the other reacts, asks, guesses and summarises, which
  repeats the content in new wording. One English sentence opens the episode.
- **Topic.** A subject the learner knows, over three days, planned as five section briefs per episode.
- **Speech.** One clip per sentence, speed 0.88, pauses between sentences, turns and sections.
- **Replay.** The first three sections of the previous episode follow the new conversation.
- **Delivery.** The existing Podcast screen.
- **No scheduling effect.** Hearing an episode records `times_heard` only.

## Pilot

Three episodes on the books and libraries of Abbasid Baghdad (`plan-baghdad-books.json`): the House of
Wisdom, paper, and Ibn al-Nadim's list. Each has about 1,320 running words and 17 minutes of new
conversation. After rewriting, no token was left outside the allowed set. Two samples of the same
opening, ElevenLabs and Gemini, are in the Podcast screen.

Not yet built: a private podcast feed, one-tap feedback, automatic daily generation, the news ladder.

## Sources

- https://www.cambridge.org/core/journals/language-teaching/article/how-much-vocabulary-is-needed-to-use-english-replication-of-van-zeeland-schmitt-2012-nation-2006-and-cobb-2007/1D217A56A2E0056E67802A6A8360FDDE
- https://scholarspace.manoa.hawaii.edu/bitstream/10125/66816/1/20_2_10125_66816_brown.pdf
- https://www.cambridge.org/core/journals/studies-in-second-language-acquisition/article/learning-vocabulary-through-reading-listening-and-viewing/415CF118C5A114BB2D9C3B79B9656AFF
- https://link.springer.com/10.1186/s40468-025-00367-8
- https://dspace.library.uvic.ca/items/2498862b-739a-486e-a6c6-baef9574fad7
- https://repository.arizona.edu/handle/10150/194002?show=full
- https://informahealthcare.com/doi/abs/10.1080/23247797.2017.1315260
- https://digital.car.chula.ac.th/pasaa/vol64/iss1/9
- https://eld.bl.uk/catalog/100094507418_0x000001
- https://revistaseug.ugr.es/index.php/portalin/article/view/27594
- https://lup.lub.lu.se/record/8524489
- https://arxiv.org/abs/2512.18362
- https://github.com/souzatharsis/podcastfy
- https://elevenlabs.io/pricing/api
- https://aistudio.google.com/docs/speech-generation
