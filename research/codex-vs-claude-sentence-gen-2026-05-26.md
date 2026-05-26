# Alif sentence-gen A/B — Claude Sonnet vs Codex `gpt-5.5`

Generated 2026-05-26T08:01:12+00:00.
`count=5` candidates per target. Same prompt/system_prompt for both providers (taken verbatim from `backend/app/services/llm.py:generate_sentences_batch`).

## Summary

| Target | Claude time | Codex time | Claude n | Codex n | Notes |
|---|---:|---:|---:|---:|---|
| `كِتَابٌ` (book) | 55.3s | 34.0s | 5 | 5 |  |
| `فَهِمَ` (to understand) | 171.7s | 27.2s | 5 | 5 |  |
| `مَكْتَبَةٌ` (library) | 83.2s | 26.1s | 5 | 5 |  |
| `حَضَارَةٌ` (civilization) | 92.2s | 27.0s | 5 | 5 |  |
| `اِسْتَطَاعَ` (to be able to) | 133.3s | 53.5s | 5 | 5 |  |
| `عَلَى الرَّغْمِ مِنْ` (despite) | 95.5s | 32.9s | 5 | 5 |  |
| `نَاسِبٌ` (suitable / appropriate) | 119.0s | 27.7s | 5 | 5 |  |
| `مُحَاوَلَةٌ` (attempt) | 155.6s | 24.7s | 5 | 5 |  |

**Totals:** Claude 905.8s, Codex 253.2s (Codex ratio: 0.28×).

---

## `كِتَابٌ` — book (beginner)

*High-freq concrete noun; easy baseline.*

### Claude Sonnet — 55.3s, 5 sentences
**Claude #1**
```
  AR: الوَلَدُ يَقْرَأُ كِتَابًا جَدِيدًا كُلَّ يَوْمٍ.
  TR: al-waladu yaqra'u kitāban jadīdan kulla yawmin.
  EN: The boy reads a new book every day.
```

**Claude #2**
```
  AR: الكِتَابُ الصَّغِيرُ جَدِيدٌ وَجَمِيلٌ جِدًّا.
  TR: al-kitābu al-ṣaghīru jadīdun wa-jamīlun jiddan.
  EN: The small book is new and very beautiful.
```

**Claude #3**
```
  AR: وَجَدَتِ البِنْتُ كِتَابًا فِي الشَّارِعِ.
  TR: wajadati al-bintu kitāban fī al-shāri'i.
  EN: The girl found a book on the street.
```

**Claude #4**
```
  AR: كِتَابُ المُعَلِّمِ كَبِيرٌ وَقَدِيمٌ جِدًّا.
  TR: kitābu al-mu'allimi kabīrun wa-qadīmun jiddan.
  EN: The teacher's book is big and very old.
```

**Claude #5**
```
  AR: الطَّالِبُ يُرِيدُ كِتَابًا جَدِيدًا لِلْمَدْرَسَةِ.
  TR: al-ṭālibu yurīdu kitāban jadīdan lil-madrasati.
  EN: The student wants a new book for school.
```

### Codex gpt-5.5 — 34.0s, 5 sentences
**Codex #1**
```
  AR: اَلْوَلَدُ قَرَأَ كِتَابًا جَدِيدًا فِي الْبَيْتِ.
  TR: al-waladu qaraʾa kitāban jadīdan fī al-bayti.
  EN: The boy read a new book at home.
```

**Codex #2**
```
  AR: وَجَدَتِ الْبِنْتُ كِتَابًا صَغِيرًا عَلَى النَّافِذَةِ.
  TR: wajadati al-bintu kitāban ṣaghīran ʿalā al-nāfidhati.
  EN: The girl found a small book on the window.
```

**Codex #3**
```
  AR: هَذَا كِتَابٌ سَهْلٌ لِلطَّالِبِ فِي الْمَدْرَسَةِ.
  TR: hādhā kitābun sahlun lil-ṭālibi fī al-madrasati.
  EN: This is an easy book for the student at school.
```

**Codex #4**
```
  AR: فِي الصَّبَاحِ، أَرَادَ الْمُعَلِّمُ كِتَابًا قَدِيمًا.
  TR: fī al-ṣabāḥi, arāda al-muʿallimu kitāban qadīman.
  EN: In the morning, the teacher wanted an old book.
```

**Codex #5**
```
  AR: رَجَعَ الرَّجُلُ إِلَى الْبَيْتِ ثُمَّ قَرَأَ كِتَابًا.
  TR: rajaʿa al-rajulu ilā al-bayti thumma qaraʾa kitāban.
  EN: The man returned home, then read a book.
```

---

## `فَهِمَ` — to understand (beginner)

*Common verb, polysemous.*

### Claude Sonnet — 171.7s, 5 sentences
**Claude #1**
```
  AR: أَنَا فَهِمْتُ مَا قَالَ المُعَلِّمُ فِي المَدْرَسَةِ.
  TR: anā fahimtu mā qāla al-muʿallimu fī al-madrasati.
  EN: I understood what the teacher said at school.
```

**Claude #2**
```
  AR: فَهِمَتِ البِنْتُ مَا كَتَبَ الطَّالِبُ فِي البَيْتِ.
  TR: fahimati al-bintu mā kataba aṭ-ṭālibu fī al-bayti.
  EN: The girl understood what the student wrote at home.
```

**Claude #3**
```
  AR: المُعَلِّمُ فَهِمَ أَنَّ الطَّالِبَ سَعِيدٌ الآنَ.
  TR: al-muʿallimu fahima anna aṭ-ṭāliba saʿīdun al-ʾāna.
  EN: The teacher understood that the student is happy now.
```

**Claude #4**
```
  AR: فَهِمْتُ أَنَّ المَدْرَسَةَ الجَدِيدَةَ كَبِيرَةٌ جِدًّا.
  TR: fahimtu anna al-madrasata al-jadīdata kabīratun jiddan.
  EN: I understood that the new school is very big.
```

**Claude #5**
```
  AR: الطَّالِبُ قَرَأَ ثُمَّ فَهِمَ مَا قَرَأَ.
  TR: aṭ-ṭālibu qaraʾa ṯumma fahima mā qaraʾa.
  EN: The student read, then understood what he read.
```

### Codex gpt-5.5 — 27.2s, 5 sentences
**Codex #1**
```
  AR: فَهِمَ الوَلَدُ فِي المَدْرَسَةِ، ثُمَّ كَتَبَ.
  TR: Fahima al-waladu fī al-madrasati, thumma kataba.
  EN: The boy understood at school, then he wrote.
```

**Codex #2**
```
  AR: الطَّالِبُ فَهِمَ، فَكَانَ سَعِيدًا جِدًّا فِي البَيْتِ.
  TR: Al-ṭālibu fahima, fa-kāna saʻīdan jiddan fī al-bayti.
  EN: The student understood, so he was very happy at home.
```

**Codex #3**
```
  AR: فِي الصَّبَاحِ، فَهِمَ المُعَلِّمُ مَا قَرَأَ الطَّالِبُ.
  TR: Fī al-ṣabāḥi, fahima al-muʻallimu mā qaraʼa al-ṭālibu.
  EN: In the morning, the teacher understood what the student read.
```

**Codex #4**
```
  AR: هَلْ فَهِمَ الرَّجُلُ مَا قَالَ الصَّدِيقُ؟
  TR: Hal fahima al-rajulu mā qāla al-ṣadīqu?
  EN: Did the man understand what the friend said?
```

**Codex #5**
```
  AR: هُنَاكَ طَالِبٌ فَهِمَ، لَكِنَّ بَعْضَ الطُّلَّابِ لَمْ يَفْهَمُوا.
  TR: Hunāka ṭālibun fahima, lākinna baʻḍa al-ṭullābi lam yafhamū.
  EN: There was a student who understood, but some students did not understand.
```

---

## `مَكْتَبَةٌ` — library (intermediate)

*Mim-prefix derived noun (place-of pattern).*

### Claude Sonnet — 83.2s, 5 sentences
**Claude #1**
```
  AR: ذَهَبَ الطَّالِبُ إِلَى المَكْتَبَةِ فِي الصَّبَاحِ.
  TR: dhahaba al-ṭālibu ilá al-maktabati fī al-ṣabāḥi.
  EN: The student went to the library in the morning.
```

**Claude #2**
```
  AR: المَكْتَبَةُ فِي المَدِينَةِ كَبِيرَةٌ وَجَمِيلَةٌ.
  TR: al-maktabatu fī al-madīnati kabīratun wa-jamīlatun.
  EN: The library in the city is big and beautiful.
```

**Claude #3**
```
  AR: تُرِيدُ البِنْتُ أَنْ تَذْهَبَ إِلَى المَكْتَبَةِ.
  TR: turīdu al-bintu an tadhaba ilá al-maktabati.
  EN: The girl wants to go to the library.
```

**Claude #4**
```
  AR: قَرَأَ الوَلَدُ فِي المَكْتَبَةِ، ثُمَّ رَجَعَ إِلَى البَيْتِ.
  TR: qaraʾa al-waladu fī al-maktabati, thumma rajaʿa ilá al-bayti.
  EN: The boy read in the library, then returned home.
```

**Claude #5**
```
  AR: رَأَيْتُ المَكْتَبَةَ الكَبِيرَةَ الَّتِي فِي المَدِينَةِ.
  TR: raʾaytu al-maktabata al-kabīrata allatī fī al-madīnati.
  EN: I saw the big library that is in the city.
```

### Codex gpt-5.5 — 26.1s, 5 sentences
**Codex #1**
```
  AR: ذَهَبَ الطَّالِبُ إِلَى مَكْتَبَةٍ كَبِيرَةٍ فِي المَدِينَةِ.
  TR: Dhahaba al-ṭālibu ilá maktabatin kabīratin fī al-madīnati.
  EN: The student went to a big library in the city.
```

**Codex #2**
```
  AR: هَذِهِ مَكْتَبَةٌ جَدِيدَةٌ، وَالبَابُ صَغِيرٌ جِدًّا.
  TR: Hādhihi maktabatun jadīdatun, wa-al-bābu ṣaghīrun jiddan.
  EN: This is a new library, and the door is very small.
```

**Codex #3**
```
  AR: فِي الشَّارِعِ مَكْتَبَةٌ قَدِيمَةٌ الَّتِي رَآهَا المُعَلِّمُ.
  TR: Fī al-shāriʻi maktabatun qadīmatun allatī raʼāhā al-muʻallimu.
  EN: On the street is an old library that the teacher saw.
```

**Codex #4**
```
  AR: قَرَأَتِ البِنْتُ فِي مَكْتَبَةٍ، ثُمَّ رَجَعَتْ إِلَى البَيْتِ.
  TR: Qaraʼati al-bintu fī maktabatin, thumma rajaʻat ilá al-bayti.
  EN: The girl read in a library, then returned to the house.
```

**Codex #5**
```
  AR: المُعَلِّمُ قَالَ إِنَّ المَدِينَةَ لَيْسَ فِيهَا مَكْتَبَةٌ.
  TR: Al-muʻallimu qāla inna al-madīnata laysa fīhā maktabatun.
  EN: The teacher said that the city does not have a library.
```

---

## `حَضَارَةٌ` — civilization (intermediate)

*Abstract noun, register-sensitive.*

### Claude Sonnet — 92.2s, 5 sentences
**Claude #1**
```
  AR: قَرَأَ المُعَلِّمُ عَنِ الحَضَارَةِ القَدِيمَةِ فِي المَدِينَةِ.
  TR: qaraʾa al-muʿallim ʿan al-ḥaḍārah al-qadīmah fī al-madīnah.
  EN: The teacher read about the ancient civilization in the city.
```

**Claude #2**
```
  AR: الطَّالِبُ دَرَسَ الحَضَارَةَ القَدِيمَةَ فِي المَدْرَسَةِ.
  TR: al-ṭālib daras al-ḥaḍārah al-qadīmah fī al-madrasah.
  EN: The student studied the ancient civilization at school.
```

**Claude #3**
```
  AR: الحَضَارَةُ القَدِيمَةُ بَدَأَتْ فِي هَذِهِ المَدِينَةِ.
  TR: al-ḥaḍārah al-qadīmah badaʾat fī hādhihi al-madīnah.
  EN: The ancient civilization began in this city.
```

**Claude #4**
```
  AR: رَأَى الرَّجُلُ بَيْتًا قَدِيمًا مِنْ حَضَارَةٍ كَبِيرَةٍ.
  TR: raʾá al-rajul baytan qadīman min ḥaḍāratin kabīratin.
  EN: The man saw an old house from a great civilization.
```

**Claude #5**
```
  AR: هَذِهِ الحَضَارَةُ كَانَتْ جَمِيلَةً وَكَبِيرَةً.
  TR: hādhihi al-ḥaḍārah kānat jamīlatan wa-kabīratan.
  EN: This civilization was beautiful and great.
```

### Codex gpt-5.5 — 27.0s, 5 sentences
**Codex #1**
```
  AR: دَرَسَ الطَّالِبُ حَضَارَةً قَدِيمَةً فِي المَدْرَسَةِ.
  TR: darasa al-ṭālibu ḥaḍāratan qadīmatan fī al-madrasati.
  EN: The student studied an old civilization at school.
```

**Codex #2**
```
  AR: المُعَلِّمُ كَتَبَ أَنَّ حَضَارَةَ المَدِينَةِ قَدِيمَةٌ.
  TR: al-muʿallimu kataba anna ḥaḍārata al-madīnati qadīmatun.
  EN: The teacher wrote that the city's civilization is old.
```

**Codex #3**
```
  AR: حَضَارَةُ هٰذِهِ المَدِينَةِ جَمِيلَةٌ، لٰكِنَّهَا صَغِيرَةٌ الآنَ.
  TR: ḥaḍāratu hādhihi al-madīnati jamīlatun, lākinahā ṣaghīratun al-āna.
  EN: The civilization of this city is beautiful, but it is small now.
```

**Codex #4**
```
  AR: فِي المَدِينَةِ حَضَارَةٌ قَدِيمَةٌ رَآهَا الرَّجُلُ.
  TR: fī al-madīnati ḥaḍāratun qadīmatun raʾāhā al-rajulu.
  EN: In the city there is an old civilization that the man saw.
```

**Codex #5**
```
  AR: قَالَتِ الاِمْرَأَةُ إِنَّ حَضَارَةً كَبِيرَةً بَدَأَتْ هُنَا.
  TR: qālati al-imraʾatu inna ḥaḍāratan kabīratan badaʾat hunā.
  EN: The woman said that a great civilization began here.
```

---

## `اِسْتَطَاعَ` — to be able to (intermediate)

*Form X verb, complex morphology + governs subjunctive.*

### Claude Sonnet — 133.3s, 5 sentences
**Claude #1**
```
  AR: اِسْتَطَاعَ الطَّالِبُ أَنْ يَذْهَبَ إِلَى المَدْرَسَةِ الجَدِيدَةِ.
  TR: istaṭāʿa al-ṭālibu ʾan yadhhaba ʾilā al-madrasati al-jadīdati.
  EN: The student was able to go to the new school.
```

**Claude #2**
```
  AR: المُعَلِّمُ لَمْ يَسْتَطِعْ أَنْ يَجِدَ البَيْتَ القَدِيمَ.
  TR: al-muʿallimu lam yastaṭiʿ ʾan yajida al-bayta al-qadīma.
  EN: The teacher was not able to find the old house.
```

**Claude #3**
```
  AR: اِسْتَطَاعَتِ البِنْتُ أَنْ تَقْرَأَ وَتَكْتُبَ فِي البَيْتِ.
  TR: istaṭāʿati al-bintu ʾan taqraʾa wa-taktuba fī al-bayti.
  EN: The girl was able to read and write at home.
```

**Claude #4**
```
  AR: الرَّجُلُ الَّذِي اِسْتَطَاعَ أَنْ يَعْمَلَ سَعِيدٌ جِدًّا.
  TR: al-rajulu alladhī istaṭāʿa ʾan yaʿmala saʿīdun jiddan.
  EN: The man who was able to work is very happy.
```

**Claude #5**
```
  AR: بَعْدَ العَمَلِ الصَّعْبِ، اِسْتَطَاعَ الوَلَدُ أَنْ يَنَامَ.
  TR: baʿda al-ʿamali al-ṣaʿbi, istaṭāʿa al-waladu ʾan yanāma.
  EN: After the difficult work, the boy was able to sleep.
```

### Codex gpt-5.5 — 53.5s, 5 sentences
**Codex #1**
```
  AR: اِسْتَطَاعَ الوَلَدُ أَنْ يَقْرَأَ فِي البَيْتِ.
  TR: Istaṭāʿa al-waladu an yaqraʾa fī al-bayti.
  EN: The boy was able to read at home.
```

**Codex #2**
```
  AR: الطَّالِبُ الَّذِي اِسْتَطَاعَ أَنْ يَدْرُسَ لَيْلًا سَعِيدٌ.
  TR: Al-ṭālibu alladhī istaṭāʿa an yadrusa laylan saʿīdun.
  EN: The student who was able to study at night is happy.
```

**Codex #3**
```
  AR: فِي الصَّبَاحِ، اِسْتَطَاعَ المُعَلِّمُ أَنْ يَكْتُبَ قَلِيلًا.
  TR: Fī al-ṣabāḥi, istaṭāʿa al-muʿallimu an yaktuba qalīlan.
  EN: In the morning, the teacher was able to write a little.
```

**Codex #4**
```
  AR: هَلْ اِسْتَطَاعَ الرَّجُلُ أَنْ يَجِدَ السَّيَّارَةَ الجَدِيدَةَ؟
  TR: Hal istaṭāʿa al-rajulu an yajida al-sayyārata al-jadīdata?
  EN: Was the man able to find the new car?
```

**Codex #5**
```
  AR: لَكِنَّ الصَّدِيقَ اِسْتَطَاعَ أَنْ يَرْجِعَ إِلَى المَدِينَةِ.
  TR: Lākinna al-ṣadīqa istaṭāʿa an yarjiʿa ilā al-madīnati.
  EN: But the friend was able to return to the city.
```

---

## `عَلَى الرَّغْمِ مِنْ` — despite (advanced)

*Compound preposition; multi-word target.*

### Claude Sonnet — 95.5s, 5 sentences
**Claude #1**
```
  AR: ذَهَبَ الطَّالِبُ إِلَى المَدْرَسَةِ عَلَى الرَّغْمِ مِنَ المَاءِ الكَثِيرِ فِي الشَّارِعِ.
  TR: dhahaba aṭ-ṭālibu ilā al-madrasati ʿalā ar-raghmi mina al-māʾi al-kathīri fī ash-shāriʿi.
  EN: The student went to school despite the abundant water in the street.
```

**Claude #2**
```
  AR: المُعَلِّمُ كَانَ سَعِيدًا عَلَى الرَّغْمِ مِنَ اليَوْمِ الصَّعْبِ.
  TR: al-muʿallimu kāna saʿīdan ʿalā ar-raghmi mina al-yawmi aṣ-ṣaʿbi.
  EN: The teacher was happy despite the difficult day.
```

**Claude #3**
```
  AR: تَأْكُلُ القِطَّةُ الصَّغِيرَةُ الطَّعَامَ عَلَى الرَّغْمِ مِنْ قِلَّتِهِ.
  TR: taʾkulu al-qiṭṭatu aṣ-ṣaghīratu aṭ-ṭaʿāma ʿalā ar-raghmi min qillatihi.
  EN: The small cat eats the food despite its scarcity.
```

**Claude #4**
```
  AR: رَأَى الرَّجُلُ السَّيَّارَةَ القَدِيمَةَ عَلَى الرَّغْمِ مِنْ صُعُوبَةِ الرُّؤْيَةِ.
  TR: raʾā ar-rajulu as-sayyārata al-qadīmata ʿalā ar-raghmi min ṣuʿūbati ar-ruʾyati.
  EN: The man saw the old car despite the difficulty of seeing.
```

**Claude #5**
```
  AR: بَدَأَ الطَّالِبُ العَمَلَ عَلَى الرَّغْمِ مِنْ قِلَّةِ النَّوْمِ.
  TR: badaʾa aṭ-ṭālibu al-ʿamala ʿalā ar-raghmi min qillati an-nawmi.
  EN: The student began working despite the lack of sleep.
```

### Codex gpt-5.5 — 32.9s, 5 sentences
**Codex #1**
```
  AR: عَلَى الرَّغْمِ مِنْ يَوْمٍ صَعْبٍ، دَرَسَ الطَّالِبُ كَثِيرًا.
  TR: ʻalá al-raghmi min yawmin ṣaʻbin, darasa al-ṭālibu kathīran.
  EN: Despite a difficult day, the student studied a lot.
```

**Codex #2**
```
  AR: المُعَلِّمُ سَعِيدٌ، عَلَى الرَّغْمِ مِنْ عَمَلٍ كَثِيرٍ.
  TR: al-muʻallimu saʻīdun, ʻalá al-raghmi min ʻamalin kathīrin.
  EN: The teacher is happy, despite a lot of work.
```

**Codex #3**
```
  AR: رَجَعَتِ البِنْتُ إِلَى البَيْتِ، عَلَى الرَّغْمِ مِنْ لَيْلٍ.
  TR: rajaʻati al-bintu ilá al-bayti, ʻalá al-raghmi min laylin.
  EN: The girl returned to the house, despite the night.
```

**Codex #4**
```
  AR: فِي المَدِينَةِ مَاءٌ قَلِيلٌ، عَلَى الرَّغْمِ مِنْ صَبَاحٍ جَمِيلٍ.
  TR: fī al-madīnati māʼun qalīlun, ʻalá al-raghmi min ṣabāḥin jamīlin.
  EN: In the city there is little water, despite a beautiful morning.
```

**Codex #5**
```
  AR: قَالَ الرَّجُلُ إِنَّ الشَّارِعَ كَبِيرٌ، عَلَى الرَّغْمِ مِنْ سَيَّارَةٍ.
  TR: qāla al-rajulu inna al-shāriʻa kabīrun, ʻalá al-raghmi min sayyāratin.
  EN: The man said that the street is big, despite a car.
```

---

## `نَاسِبٌ` — suitable / appropriate (advanced)

*Active participle as adjective.*

### Claude Sonnet — 119.0s, 5 sentences
**Claude #1**
```
  AR: إِنَّ هَذِهِ المَدْرَسَةَ نَاسِبَةٌ لِلطُّلَّابِ الصِّغَارِ.
  TR: inna hādhihi l-madrasata nāsibatun li-ṭ-ṭullābi ṣ-ṣighāri.
  EN: Indeed, this school is suitable for the small students.
```

**Claude #2**
```
  AR: وَجَدَتِ المَرْأَةُ بَيْتًا نَاسِبًا فِي المَدِينَةِ الجَدِيدَةِ.
  TR: wajadati l-marʾatu baytan nāsiban fī l-madīnati l-jadīdati.
  EN: The woman found a suitable house in the new city.
```

**Claude #3**
```
  AR: لَيْسَ الطَّعَامُ القَدِيمُ نَاسِبًا لِلْأَوْلَادِ الصِّغَارِ.
  TR: laysa ṭ-ṭaʿāmu l-qadīmu nāsiban li-l-awlādi ṣ-ṣighāri.
  EN: The old food is not suitable for the small children.
```

**Claude #4**
```
  AR: كَأَنَّ هَذَا الشَّارِعَ نَاسِبٌ لِلسَّيَّارَاتِ الصَّغِيرَةِ فَقَطْ.
  TR: kaʾanna hādhā sh-shāriʿa nāsibun li-s-sayyārāti ṣ-ṣaghīrati faqaṭ.
  EN: It seems this street is suitable only for small cars.
```

**Claude #5**
```
  AR: لَمْ يَرَ الطَّالِبُ بَيْتًا نَاسِبًا فِي الشَّارِعِ القَدِيمِ.
  TR: lam yara ṭ-ṭālibu baytan nāsiban fī sh-shāriʿi l-qadīmi.
  EN: The student did not see a suitable house on the old street.
```

### Codex gpt-5.5 — 27.7s, 5 sentences
**Codex #1**
```
  AR: إِنَّ بَيْتَ الرَّجُلِ الجَدِيدَ نَاسِبٌ لِلْمُعَلِّمِ.
  TR: Inna bayta al-rajuli al-jadīda nāsibun lil-muʿallimi.
  EN: The man's new house is suitable for the teacher.
```

**Codex #2**
```
  AR: وَجَدَ الْمُعَلِّمُ طَعَامًا قَلِيلًا، لَكِنَّهُ نَاسِبٌ لِلْوَلَدِ.
  TR: Wajada al-muʿallimu ṭaʿāman qalīlan, lakinnahu nāsibun lil-waladi.
  EN: The teacher found a little food, but it is suitable for the boy.
```

**Codex #3**
```
  AR: فِي هٰذَا الصَّبَاحِ مَاءٌ نَاسِبٌ لِلطَّالِبِ.
  TR: Fī hādhā al-ṣabāḥi māʾun nāsibun lil-ṭālibi.
  EN: This morning there is water suitable for the student.
```

**Codex #4**
```
  AR: قَالَ الطَّالِبُ إِنَّ هٰذَا الْيَوْمَ نَاسِبٌ لِلدَّرْسِ.
  TR: Qāla al-ṭālibu inna hādhā al-yawma nāsibun lil-darsi.
  EN: The student said that this day is suitable for study.
```

**Codex #5**
```
  AR: لَيْسَ الشَّارِعُ الْقَدِيمُ نَاسِبًا، لَكِنَّ الْجَدِيدَ نَاسِبٌ.
  TR: Laysa al-shāriʿu al-qadīmu nāsiban, lakinna al-jadīda nāsibun.
  EN: The old street is not suitable, but the new one is suitable.
```

---

## `مُحَاوَلَةٌ` — attempt (intermediate)

*Form III verbal noun (maṣdar).*

### Claude Sonnet — 155.6s, 5 sentences
**Claude #1**
```
  AR: ذَهَبَ الرَّجُلُ إِلَى المَدْرَسَةِ في مُحَاوَلَةٍ لِإِيجَادِ المُعَلِّمِ.
  TR: ḏahaba ar-rajulu ilā al-madrasati fī muḥāwalatin li-ījādi al-muʿallimi.
  EN: The man went to the school in an attempt to find the teacher.
```

**Claude #2**
```
  AR: كَانَتْ مُحَاوَلَتُهُ لِلْعَمَلِ في المَدِينَةِ صَعْبَةً جِدًّا.
  TR: kānat muḥāwalatuhu lil-ʿamali fī al-madīnati ṣaʿbatan jiddan.
  EN: His attempt to work in the city was very difficult.
```

**Claude #3**
```
  AR: رَجَعَ الوَلَدُ إِلَى البَيْتِ بَعْدَ مُحَاوَلَةٍ لِإِيجَادِ القِطَّةِ.
  TR: rajaʿa al-waladu ilā al-bayti baʿda muḥāwalatin li-ījādi al-qiṭṭati.
  EN: The boy returned home after an attempt to find the cat.
```

**Claude #4**
```
  AR: سَمِعَ المُعَلِّمُ عَنْ مُحَاوَلَةِ الطَّالِبِ الصَّغِيرِ الجَدِيدَةِ.
  TR: samiʿa al-muʿallimu ʿan muḥāwalati aṭ-ṭālibi aṣ-ṣaġīri al-jadīdati.
  EN: The teacher heard about the small student's new attempt.
```

**Claude #5**
```
  AR: أَرَادَ الرَّجُلُ مُحَاوَلَةً أُخْرَى فَبَدَأَ العَمَلَ في الصَّبَاحِ.
  TR: arāda ar-rajulu muḥāwalatan uḫrā fa-badaʾa al-ʿamala fī aṣ-ṣabāḥi.
  EN: The man wanted another attempt, so he began the work in the morning.
```

### Codex gpt-5.5 — 24.7s, 5 sentences
**Codex #1**
```
  AR: بَدَأَ المُعَلِّمُ مُحَاوَلَةً جَدِيدَةً فِي المَدْرَسَةِ.
  TR: Badaʾa al-muʿallimu muḥāwalatan jadīdatan fī al-madrasati.
  EN: The teacher began a new attempt at the school.
```

**Codex #2**
```
  AR: مُحَاوَلَةُ الطَّالِبِ الَّذِي دَرَسَ كَثِيرًا صَعْبَةٌ.
  TR: Muḥāwalatu al-ṭālibi alladhī darasa kathīran ṣaʿbatun.
  EN: The attempt of the student who studied a lot is difficult.
```

**Codex #3**
```
  AR: هُنَاكَ مُحَاوَلَةٌ قَلِيلَةٌ فِي هٰذَا اليَوْمِ.
  TR: Hunāka muḥāwalatun qalīlatun fī hādhā al-yawmi.
  EN: There is a small attempt on this day.
```

**Codex #4**
```
  AR: رَأَى الرَّجُلُ مُحَاوَلَةَ الوَلَدِ، لٰكِنَّها كَانَتْ صَعْبَةً.
  TR: Raʾā al-rajulu muḥāwalata al-waladi, lākinahā kānat ṣaʿbatan.
  EN: The man saw the boy’s attempt, but it was difficult.
```

**Codex #5**
```
  AR: فِي الصَّبَاحِ، قَالَتِ البِنْتُ إِنَّ المُحَاوَلَةَ سَهْلَةٌ.
  TR: Fī al-ṣabāḥi, qālati al-bintu inna al-muḥāwalata sahlā.
  EN: In the morning, the girl said that the attempt is easy.
```

---
