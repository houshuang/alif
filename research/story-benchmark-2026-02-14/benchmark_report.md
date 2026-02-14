# Story Generation Benchmark Report

**Date**: 2026-02-14 10:41
**Stories generated**: 32 successful / 32 total
**Models**: gemini, openai, opus, sonnet
**Strategies**: A, B, C, D

## Results by Model

| Model | Composite | Narrative | Interest | Natural | Compliance% | Unknown | Cost/story | Time(s) |
|-------|-----------|-----------|----------|---------|-------------|---------|------------|---------|
| gemini | 3.03 | 3.0 | 2.8 | 2.8 | 72% | 9.9 | $0.0009 | 6.2 |
| openai | 2.63 | 3.0 | 2.0 | 1.6 | 82% | 9.4 | $0.0179 | 19.0 |
| opus | 3.73 | 4.0 | 2.9 | 3.2 | 74% | 11.6 | $0.1554 | 24.8 |
| sonnet | 3.90 | 4.0 | 2.8 | 3.8 | 57% | 16.5 | $0.0270 | 19.1 |

## Results by Strategy

| Strategy | Composite | Narrative | Interest | Compliance% | Unknown |
|----------|-----------|-----------|----------|-------------|---------|
| A (Baseline (flat vocab, random g) | 3.57 | 3.6 | 2.9 | 76% | 9.1 |
| B (POS-grouped vocabulary) | 3.39 | 3.6 | 2.8 | 77% | 8.4 |
| C (Expanded narrative structures) | 2.90 | 3.0 | 1.9 | 78% | 8.9 |
| D (Two-pass (generate freely, the) | 3.44 | 3.8 | 2.9 | 54% | 21.0 |

## Model × Strategy Matrix (Composite Score)

| Model | A | B | C | D |
|-------|------|------|------|------|
| gemini | 3.53 (76%) | 3.47 (77%) | 2.30 (77%) | 2.83 (59%) |
| openai | 2.52 (87%) | 2.75 (86%) | 2.65 (87%) | 2.60 (67%) |
| opus | 4.17 (84%) | 3.58 (79%) | 3.25 (82%) | 3.92 (52%) |
| sonnet | 4.05 (57%) | 3.75 (68%) | 3.40 (66%) | 4.40 (36%) |

## Top 5 Stories

### #1 — sonnet / Strategy D (composite 4.75, compliance 33%)
**The Old Message** (الرِّسَالَةُ القَدِيمَةُ)

> وَجَدَ عَلِيٌّ رِسَالَةً قَدِيمَةً فِي بَيْتِ أَبِيهِ. فَتَحَهَا، فَكَانَتْ رِسَالَةً كَتَبَهَا أَبُوهُ لِأُمِّهِ قَبْلَ سَنَوَاتٍ كَثِيرَةٍ. قَرَأَهَا بِصَوْتٍ عَالٍ لِأُمِّهِ الَّتِي كَانَتْ سَعِيدَةً وَلَكِنْ بَكَتْ. ثُمَّ قَالَتْ لَهُ: «لَمْ يُعْطِهَا لِي، وَلَكِنَّهُ عَاشَ كُلَّ كَلِمَةٍ فِيهَا». فِي تِلْكَ اللَّحْظَةِ فَهِمَ عَلِيٌّ أَنَّ الحُبَّ الحَقِيقِيَّ لَيْسَ مَا نَقُولُهُ، بَلْ مَا نَعِيشُهُ فِي الحَيَاةِ.

*Ali found an old message in his father's house. He opened it, and it was a message his father had written to his mother many years ago. He read it aloud to his mother, who was happy but cried. Then she said to him: 'He did not give it to me, but he lived every word in it.' In that moment, Ali understood that true love is not what we say, but what we live in life.*

Unknown words: قديمة, ابيه, فتحها, كتبها, ابوه, سنوات, كثيرة, قراها, بصوت, عال
Judge: An excellent example of a learner-focused story that maintains high literary quality. The narrative arc is complete and emotionally resonant. The Arabic is natural, using appropriate connectors like 'فـ' and 'لكن', and the diacritics are accurately placed for an educational context.

### #2 — opus / Strategy D (composite 4.55, compliance 45%)
**The Old House** (البَيْتُ القَدِيمُ)

> وَقَفَ طَارِقٌ أَمَامَ بَيْتِهِ القَدِيمِ، فَاقْتَرَبَ مِنْهُ جَارُهُ سَمِيرٌ وَقَالَ: «هَذَا البَيْتُ دِمْنَةٌ يَا طَارِقُ، لَيْسَ مُهِمًّا.» لَكِنَّ طَارِقًا قَالَ: «أَبِي بَنَاهُ، وَكُلُّ مَوْضِعٍ فِيهِ رِسَالَةٌ مِنْ حَيَاتِهِ.» قَالَ سَمِيرٌ: «الحَيَاةُ فِي القَلْبِ لَا فِي الحِجَارَةِ، وَبَيْتٌ قَدِيمٌ قَدْ يَسْقُطُ عَلَيْكُمْ!» صَمَتَ طَارِقٌ طَوِيلًا، ثُمَّ قَالَ: «لَنْ أَهْدِمَهُ، وَلَكِنْ سَأَجْعَلُهُ جَدِيدًا — فَبَعْضُ الأَشْيَاءِ جَمِيلَةٌ وَتَسْتَحِقُّ الحَيَاةَ.» فَهِمَ سَمِيرٌ أَنَّ طَارِقًا لَمْ يَكُنْ يَتَكَلَّمُ عَنِ البَيْتِ فَقَطْ.

*Tariq stood before his old house. His neighbor Samir approached and said: "This house is a ruin, Tariq — it's not important." But Tariq said: "My father built it, and every place in it is a message from his life." Samir said: "Life is in the heart, not in stones, and an old house could collapse on you!" Tariq was silent for a long while, then said: "I won't tear it down, but I will make it new — some things are beautiful and deserve life." Samir understood that Tariq had not been talking about the house alone.*

Unknown words: وقف, طارق, فاقترب, سمير, مهما, طارقا, ابي, وكل, فيه, القلب
Judge: The story is well-structured with a clear philosophical subtext. The use of the word 'دمنة' (ruin/remains) is a sophisticated literary touch. The grammar and diacritics are flawless, making it an excellent resource for learners.

### #3 — opus / Strategy A (composite 4.30, compliance 93%)
**The Smart Parrot** (البَبَّغَاءُ الذَّكِيُّ)

> كَانَ عِنْدَ عَلِيٍّ بَبَّغَاءٌ جَمِيلٌ. قَالَ عَلِيٌّ لِلْبَبَّغَاءِ كُلَّ يَوْمٍ: "أَنَا مُهَنْدِسٌ ذَكِيٌّ!" ثُمَّ جَاءَ جَارٌ جَدِيدٌ إِلَى البَيْتِ القَرِيبِ. قَالَ الجَارُ: "أَهْلاً، أَنَا مُحَمَّدٌ، تَشَرَّفْنَا!" فَقَالَ البَبَّغَاءُ: "أَنَا مُهَنْدِسٌ ذَكِيٌّ!" قَالَ مُحَمَّدٌ: "تَمَامًا يَا بَبَّغَاءُ... وَأَنَا زَرَافَةٌ!"

*Ali had a beautiful parrot. Every day, Ali said to the parrot: "I am a smart engineer!" Then a new neighbor came to the nearby house. The neighbor said: "Hello, I am Muhammad, nice to meet you!" So the parrot said: "I am a smart engineer!" Muhammad said: "Absolutely, parrot... and I am a giraffe!"*

Unknown words: يوم, جاء
Judge: A charming and humorous story for learners. The grammar and diacritics are flawless. The punchline is culturally and linguistically natural, though the phrase 'إلى البيت القريب' is slightly redundant but acceptable for a learner's level.

### #4 — opus / Strategy A (composite 4.05, compliance 75%)
**The Animal in the Room** (الحَيَوَانُ فِي الغُرْفَةِ)

> عَلِيٌّ مُعَلِّمٌ فِي قَرْيَةٍ قَدِيمَةٍ. فِي يَوْمٍ بَارِدٍ، سَمِعَ صَوْتاً فِي مَطْبَخِ بَيْتِهِ. قَالَ لِأَخِيهِ مُحَمَّدٍ: «هُنَاكَ حَيَوَانٌ كَبِيرٌ فِي المَطْبَخِ! هَلْ هُوَ نَمِرٌ؟» ثُمَّ دَبَّ مُحَمَّدٌ إِلَى المَطْبَخِ وَفَتَحَ البَابَ. وَلَكِنْ لَمْ يَكُنْ نَمِراً — كَانَتْ قِطَّةً سَوْدَاءَ صَغِيرَةً عَلَى الطَّاوِلَةِ، وَمَعَهَا فَأْرٌ! قَالَ مُحَمَّدٌ: «هَذَا هُوَ النَّمِرُ الكَبِيرُ يَا عَلِيُّ؟» فَكَانَ عَلِيٌّ سَعِيداً وَقَالَ: «القِطَّةُ جَمِيلَةٌ... هِيَ لِي الآنَ!»

*Ali is a teacher in an old village. On a cold day, he heard a sound in his house's kitchen. He said to his brother Mohamed: "There is a big animal in the kitchen! Is it a tiger?" Then Mohamed crept to the kitchen and opened the door. But it was not a tiger — it was a small black cat on the table, and with it was a mouse! Mohamed said: "This is the big tiger, Ali?" Ali was happy and said: "The cat is beautiful... she is mine now!"*

Unknown words: قديمة, يوم, سمع, صوتا, لاخيه, وفتح, الباب, نمرا, —, صغيرة
Judge: The story is well-structured for a learner's text. The grammar and diacritics are excellent. The use of the verb 'دبّ' (crept/crawled) is a bit unusual for a human approaching a kitchen door—'تسلّل' (sneaked) would be more natural—but it is grammatically correct. The narrative provides a clear setup and a humorous resolution.

### #5 — sonnet / Strategy A (composite 4.05, compliance 59%)
**The Smart Frog** (الضِّفْدَعُ الذَّكِيُّ)

> كَانَ ضِفْدَعٌ صَغِيرٌ يَسْكُنُ فِي حَدِيقَةٍ جَمِيلَةٍ. رَأَى الضِّفْدَعُ ذُبَابَةً كَبِيرَةً، فَقَالَ: "أَنَا جَائِعٌ جِدًّا!" قَفَزَ الضِّفْدَعُ عَلَى الذُّبَابَةِ، وَلَكِنَّ الذُّبَابَةَ طَارَتْ بِسُرْعَةٍ. ثُمَّ رَأَى الضِّفْدَعُ دُودًا طَوِيلًا عَلَى الْعُشْبِ، فَقَالَ: "هَذَا سَهْلٌ!" أَكَلَ الدُّودَ بِسُرْعَةٍ، ثُمَّ قَالَ: "الدُّودُ لَا يَطِيرُ، الدُّودُ ذَكِيٌّ مِثْلِي!"

*There was a small frog living in a beautiful garden. The frog saw a big fly, so he said: "I'm very hungry!" The frog jumped at the fly, but the fly flew away quickly. Then the frog saw a long worm on the grass, so he said: "This is easy!" He ate the worm quickly, then said: "Worms don't fly—worms are smart like me!"*

Unknown words: صغير, يسكن, راى, جائع, قفز, طارت, بسرعة, دودا, طويلا, اكل
Judge: The story is grammatically sound and the diacritics are perfectly placed for a learner. While the narrative is simple, the ending provides a humorous touch of irony. The Arabic phrasing is natural for a children's fable or beginner reader.

## Bottom 5 Stories

### #1 — openai / Strategy D (composite 2.50, compliance 56%)
**Noor and the Old House** (نُورٌ وَالبَيْتُ القَدِيمُ)

> وَقَفَتْ نُورٌ عِنْدَ البَيْتِ القَدِيمِ، وَقالَتْ: «بِلا، لا، هٰذا مُهِمٌّ لَنا». فَقالَ سامِرٌ: «رَأْيِي تَماماً: هٰذا غالٍ، وَعِنْدِي هٰذا أَكْبَرُ». ثُمَّ قالَتْ نُورٌ: «في اللَّيْلِ سَتَسْمَعُ رِيحًا قَرِيبَةً عِنْدَ البَيْتِ، وَسَتَفْهَمُ أَنَّ هٰذا مُهِمٌّ». وَقالَ سامِرٌ: «نَعَم، مُمْكِن، وَلكِنْ أَنا سَعيدٌ مَعَ البَيْتِ الجَدِيدِ». ثُمَّ أَعْطَتْ نُورٌ سامِرًا رِسالَةً، وَقالَتْ هَمْسًا: «هٰذِهِ رِسالَةٌ مِنْ أُمٍّ، وَفيها بِنا». فَسَكَتَ سامِرٌ طَويلًا، ثُمَّ قالَ: «بِلا، لا؛ نَحْنُ لا، وَهٰذا البَيْتُ القَدِيمُ مُهِمٌّ لَنا».

Unknown words: وقفت, وقالت, لنا, سامر, رايي, غال, وعندي, قالت, الليل, ستسمع
Judge: The story suffers from unnatural phrasing and awkward word choices. The use of 'Bala' (بلى) is grammatically incorrect here as it is used to answer negative questions, not as a general 'No'. Phrases like 'In it is about us' (فيها بنا) and 'My opinion is absolute' (رأيي تماماً) do not follow native Arabic collocations. While the diacritics are excellent, the dialogue feels like a literal translation from another language rather than organic Arabic.

### #2 — openai / Strategy C (composite 2.40, compliance 90%)
**Ali’s Ball and the Rooster** (كُرَةُ عَلِيٍّ وَالدِّيكُ)

> كانَ عَلِيٌّ فِي حَدِيقَةٍ جَميلَةٍ، وَكانَتِ الشَّمْسُ قَرِيبَةً مِنَ السَّماءِ. أَعْطَى عَلِيٌّ كُرَةً حَمْراءَ لِأُمٍّ، ثُمَّ دَبَّتِ الكُرَةُ إِلَى مَوْضِعٍ قَرِيبٍ مِنْ دِمْنَةٍ قَديمَةٍ. قالَ عَلِيٌّ: «مُمْكِن؟»، فَقَدَمَ إِلَى ذٰلِكَ المَوْضِعِ بِلا رِيحٍ. دَبَّ دِيكٌ أَسْوَدُ مِنَ الدِّمْنَةِ، وَأَعْطَى عَلِيًّا رِسَالَةً. كانَ عُنْوانُ الرِّسَالَةِ: «أَهْلاً»، فَكانَ عَلِيٌّ سَعيدًا تَمامًا.

Unknown words: دبت, قديمة, عليا, سعيدا
Judge: The story feels like a collection of sentences designed to use specific vocabulary rather than a natural narrative. Phrases like 'the sun was close to the sky' are nonsensical. The use of 'دبّت' (crept/crawled) for a ball and 'دمنة' (ruin/dung heap) is unnatural. 'ممكن؟' as a standalone question in this context is a literal translation of 'Is it possible?' which doesn't work in Arabic here.

### #3 — gemini / Strategy C (composite 2.35, compliance 73%)
**Ali and the Cat** (عَلِيٌّ وَالقِطَّةُ)

> فِي يَوْمٍ جَمِيلٍ، كَانَ عَلِيٌّ فِي حَدِيقَةٍ كَبِيرَةٍ. رَأَى عَلِيٌّ قِطَّةً بَيْضَاءَ جَمِيلَةً مَعَ كُرَةٍ زَرْقَاءَ. أَعْطَى عَلِيٌّ الكُرَةَ لِلْقِطَّةِ، وَلَكِنْ دَبَّ فَأْرٌ صَغِيرٌ بَيْنَ الزُّهُورِ. تَرَكَتِ القِطَّةُ الكُرَةَ ثُمَّ قَالَتْ لِلْفَأْرِ: "أَهْلًا! مَعَ السَّلَامَةِ!". كَانَ الرَّأْيُ أَنَّ القِطَّةَ ذَكِيَّةٌ وَسَعِيدَةٌ فِي الحَيَاةِ.

Unknown words: يوم, راى, بيضاء, زرقاء, صغير, تركت, قالت, ذكية, وسعيدة
Judge: NARRATIVE_ARC: Has a basic setup (Ali in garden, sees cat) but lacks clear development and resolution. The ending ('the view was that the cat was smart') feels tacked on rather than earned. INTERESTINGNESS: The premise is simple but the cat greeting then immediately saying goodbye to the mouse is confusing rather than engaging. No real conflict or payoff. NATURALNESS: 'دَبَّ فَأْرٌ' (crawled a mouse) is acceptable but 'ظَهَرَ' might be more natural. The phrase 'كَانَ الرَّأْيُ أَنَّ' (the view was that) is awkward and unnatural for a children's story - sounds like academic writing. COHERENCE: The logical flow breaks down: why does Ali give the ball to the cat? Why does the cat greet AND say goodbye simultaneously? The final sentence about 'the view' doesn't connect to the narrative. GRAMMAR: Generally solid - proper case endings, verb agreements, idafa constructions correct. Minor issue: 'مَعَ كُرَةٍ' could be clearer as 'تَلْعَبُ بِكُرَةٍ'. DIACRITICS: Well done overall, though 'الرَّأْيُ' should be 'الرَّأْيَ' (accusative after كَانَ). TRANSLATION: Mostly accurate but 'The view was that' is literal rather than natural English. The Arabic awkwardness carries over.

### #4 — gemini / Strategy C (composite 2.25, compliance 81%)
**Ali and the Parrot** (عَلِيٌّ وَالبَبَّغَاءُ)

> عَلِيٌّ وَلَدٌ ذَكِيٌّ، وَلَكِنَّ عِنْدَهُ بَبَّغَاءٌ جَمِيلٌ. قَالَ عَلِيٌّ: «هَذَا البَبَّغَاءُ أَزْرَقُ مِثْلُ السَّمَاءِ». قَالَتْ أُخْتُهُ: «لا، هُوَ أَخْضَرُ مِثْلُ العُشْبِ». قَالَ عَلِيٌّ: «رَأْيِي أَنَّ لَوْنَهُ أَزْرَقُ جِدّاً، وَهُوَ طَائِرٌ غَالِي». قَالَتْ البِنْتُ: «لَيْسَ أَزْرَقَ، هَلْ أَنْتَ سَعِيدٌ يَا عَلِيُّ؟ هُوَ بَبَّغَاءٌ بَنِيٌّ الآنَ!». نَظَرَ عَلِيٌّ إِلَى الطَّائِرِ، ثُمَّ قَالَ: «نَعَمْ، هُوَ فِي مَطْبَخِ بَيْتِنَا مَعَ المَطَرِ، وَلَوْنُهُ جَدِيدٌ».

Unknown words: قالت, اخضر, رايي, وهو, طائر, نظر, الطائر
Judge: The story has a weak narrative structure. It begins with a color debate about a parrot but ends confusingly with the parrot being in the kitchen 'with the rain' and having a 'new color.' The phrase 'وَلَكِنَّ عِنْدَهُ' is awkward (should be 'وَعِنْدَهُ' or 'وَلَكِنْ عِنْدَهُ'). The ending is incoherent—why is the parrot brown? What does 'with the rain' mean in the kitchen? The translation struggles with this too, adding '(mud/dripping)' to clarify, suggesting the Arabic itself is unclear. 'لَوْنُهُ جَدِيدٌ' (its color is new) is an unnatural expression. The diacritics are mostly correct. Grammar is acceptable but not flawless. The story lacks a satisfying resolution and the logic breaks down at the end, making it feel like a vocabulary exercise rather than a coherent narrative.

### #5 — gemini / Strategy D (composite 2.25, compliance 57%)
**An Important Message** (رِسَالَةٌ مُهِمَّةٌ)

> مَازِنٌ رَجُلٌ سَعِيدٌ مَعَ زَوْجَتِهِ لَيْلَى فِي بَيْتٍ جَمِيلٍ. قَالَ مَازِنٌ لَهَا: «أَنْتِ بِنْتٌ لَيْسَتْ ذَكِيَّةً، لَقَدْ ضَاعَ عُنْوَانُ الجَزِيرَةِ الكَبِيرَةِ!» لَكِنَّ لَيْلَى كَانَتْ عِنْدَ الطَّاوِلَةِ مَعَ قَهْوَةٍ بَارِدَةٍ وَقَالَتْ: «أَنَا عِنْدِي العُنْوَانُ يَا مَازِنُ، وَلَكِنْ أَنْتَ نَسِيتَ أَنَّ البَيْتَ هُنَاكَ غَالِي جِدًّا.» كَانَ مَازِنٌ فِي مَوْضِعٍ صَعْبٍ ثُمَّ قَالَ: «هَلْ هَذَا رَأْيُكِ؟» قَالَتْ لَيْلَى: «نَعَمْ، الرِّسَالَةُ عِنْدِي الآنَ، تَفَضَّلْ لِنَكُونَ فِي سَلَامَةٍ.»

Unknown words: مازن, زوجته, ليلى, ذكية, ضاع, قهوة, باردة, وقالت, عندي, نسيت
Judge: NARRATIVE_ARC: The story has a setup (couple in house) but lacks clear development and resolution. The conflict about the island address appears suddenly and resolves ambiguously. INTERESTINGNESS: The premise is confusing - why does Mazen insult his wife over a lost address? The 'message' and 'safety' references at the end are unclear. NATURALNESS: Several unnatural expressions: 'أَنْتِ بِنْتٌ لَيْسَتْ ذَكِيَّةً' (calling wife 'girl' + awkward negation), 'فِي مَوْضِعٍ صَعْبٍ' (literal translation of 'difficult position'), 'لِنَكُونَ فِي سَلَامَةٍ' (awkward phrasing). COHERENCE: Logical gaps - the connection between the lost address, the expensive house, and the final 'message' and 'safety' is unclear. The conversation doesn't flow naturally. GRAMMAR: Mostly correct but 'أَنْتِ بِنْتٌ لَيْسَتْ ذَكِيَّةً' should be 'لَسْتِ ذَكِيَّةً' or 'أَنْتِ بِنْتٌ غَيْرُ ذَكِيَّةٍ'. DIACRITICS: Generally well-applied with only minor issues. TRANSLATION: Mostly accurate but doesn't capture the awkwardness of the Arabic; 'please come so we can be in safety' is odd in both languages.

## Cost Summary

**Total generation cost**: $1.6092

- gemini: $0.0072 (8 stories, $0.0009/story)
- openai: $0.1431 (8 stories, $0.0179/story)
- opus: $1.2429 (8 stories, $0.1554/story)
- sonnet: $0.2160 (8 stories, $0.0270/story)

## Most Common Unknown Words

- يوم (appeared in 9 stories)
- راى (appeared in 8 stories)
- سعيدا (appeared in 8 stories)
- قالت (appeared in 7 stories)
- نظر (appeared in 5 stories)
- قديمة (appeared in 5 stories)
- دودا (appeared in 5 stories)
- صغير (appeared in 4 stories)
- عندي (appeared in 4 stories)
- لك (appeared in 4 stories)
- ضحك (appeared in 4 stories)
- رايي (appeared in 4 stories)
- جاء (appeared in 4 stories)
- يسكن (appeared in 4 stories)
- اخضر (appeared in 3 stories)
- ذهب (appeared in 3 stories)
- نمرا (appeared in 3 stories)
- صغيرة (appeared in 3 stories)
- بسرعة (appeared in 3 stories)
- طويلا (appeared in 3 stories)
