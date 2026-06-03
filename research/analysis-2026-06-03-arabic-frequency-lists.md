# Lemmatized Arabic frequency lists — deep-research sweep (2026-06-03)

Run: `wf_bc3c3923` · 101 agents · 18 sources fetched · 25 claims verified (20 confirmed, 5 refuted).

## Verdict

For a learner targeting BOTH modern standard AND classical/Quranic Arabic, the evidence points to a two-track strategy rather than a single list. The strongest, freely-obtainable, genuinely LEMMA-KEYED resources are Quran-specific: the Quranic Arabic Corpus (corpus.quran.com — 3,680 lemmas, frequency-ranked, GPL, downloadable v0.4 on verified Tanzil text) and the newer QuranMorph corpus (sina.birzeit.edu/quran / arXiv 2506.18148 — full Quran manually lemmatized + POS-tagged by three linguists against the Qabas lexicon, 4,616 lemmas). On the MSA side, every general-frequency candidate vetted (Buckwalter & Parkinson, Aralex, Kelly/Leeds, SUBTLEX-AR) is built on contemporary corpora (newspapers, web, subtitles) with NO classical/Quranic signal, and the highest-quality one (Buckwalter & Parkinson's printed lemma list) has no downloadable underlying dataset. Recommendation: compute a dedicated Quran frequency track from corpus.quran.com (best ready-made ranked lemma list) and/or QuranMorph (best lemma annotation quality), and pair it with an obtainable MSA list — the Leeds Kelly list (free .xls, CEFR-leveled, but imperfect lemmatization) and SUBTLEX-AR (free OSF download, includes lemma+POS frequencies) being the two reusable MSA options. A separate Quran track is necessary and justified because the leading MSA dictionaries explicitly exclude classical/Quranic material.

## Findings (adversarially verified)

### [high · 3-0 (across constituent claims 1,2,3,4,5)]
A dedicated Quran-specific frequency track is necessary and should be computed separately, because the corpus.quran.com Quranic Arabic Corpus already provides a frequency-ranked, lemma-keyed list (lemmas group inflected forms, not surface forms) of a bounded 3,680-lemma vocabulary, openly licensed (GNU GPL v3, downloadable v0.4 on verified Tanzil text) and integrable into a personal app.

**Evidence:** corpus.quran.com/lemmas.jsp shows 'Lemmas 1 to 50 of 3680', frequency-ranked, with the definitional note 'A lemma groups word-forms that differ only by inflectional ... morphology, and do not vary in meaning.' The manually-verified Dukes & Habash (LREC 2010) corpus is GPL-licensed (corpus.quran.com/license.jsp) and the v0.4 annotation built on Tanzil text is downloadable (corpus.quran.com/download/). One nuance: the corpus's own usage notice says 'CHANGING IT IS NOT ALLOWED' with required source-attribution/link, which sits in tension with formal GPLv3 modification rights — relevant if redistributing a derivative. The 3,680 figure is annotation-scheme-specific (not THE canonical Quran vocabulary size). This is the best READY-MADE ranked lemma list for a Quran track.

- https://corpus.quran.com/lemmas.jsp
- https://corpus.quran.com/download/
- https://corpus.quran.com/license.jsp

### [high · 3-0 (constituent claims 8,9,19)]
QuranMorph is a stronger lemma-keyed alternative/complement for the Quran track: the full Quran (77,429 tokens) was manually lemmatized and POS-tagged by three expert linguists against the Qabas dictionary lexicon, yielding 4,616 unique lemmas (3,057 noun, 1,479 verb, 173 functional) vs 19,009 surface words — i.e. genuine dictionary-lemma keying, exactly the #1 lemmatization-quality requirement.

**Evidence:** arXiv 2506.18148 (Akra, Hammouda & Jarrar, Birzeit/SinaLab, 2025) Table 1 confirms 77,429 tokens, 19,009 unique words, 4,616 unique lemmas (3,057 noun / 1,479 verb / 173 functional). Abstract: 'Each token ... was manually lemmatized and tagged with its part-of-speech by three expert linguists', lemmas drawn from the Qabas lexicon (~58-60K lemmas, 110 lexicons) using the fine-grained 40-tag SAMA/Qabas tagset. It is a per-token annotated corpus (not a pre-computed frequency list), but lemma annotations make computing a true lemma-frequency track straightforward. CAVEAT: the specific claim that QuranMorph context-disambiguates homographs (e.g. daraba senses) was REFUTED (1-2), and the precise license (CC-BY-4.0) was also REFUTED (1-2) — open-source availability at sina.birzeit.edu/quran holds, but verify exact license terms before redistribution.

- https://arxiv.org/pdf/2506.18148
- https://sina.birzeit.edu/quran

### [high · 2-0 / 3-0 (constituent claims 13,14,15)]
The Leeds Kelly Arabic (M3) list is a freely-obtainable MSA option: downloadable directly as ar_m3.xls (~8,894 rows, ipm frequency + CEFR A1-C2 level, CC BY-NC-SA), but its coverage is general-web MSA (web-as-corpus search-engine snapshot, ~2006), NOT classical/Quranic, and its lemmatization is imperfect (mixes some inflected/plural surface forms).

**Evidence:** Direct download of ar_m3.xls confirmed: a real 4.86MB binary .xls, sheet 'Arabic', 8,894 rows x 12 cols, with ipm Freq, CEFR level, original word, and اسم/صفة/فعل grammar tags (e.g. آخر A1 1801.91). Sharoff's Kelly page states lists carry per-word CEFR A1-C2 levels selected 'using pedagogically relevant principles following the CEFR', built from 'a large snapshot of texts available ... on the Web, using ... automated search engine queries' — underlying arWaC is deduplicated MSA web text with colloquial dropped, no classical/Quranic component. Lemmatization purity is imperfect (e.g. آثار listed separately); server occasionally times out on port 80. Best for: an obtainable, CEFR-graded MSA scaffold, with the caveat that counts can land on inflected forms.

- http://corpus.leeds.ac.uk/serge/kelly/
- https://ssharoff.github.io/kelly/

### [high · 3-0 (constituent claims 10,11,12)]
SUBTLEX-AR is the other freely-downloadable MSA option and uniquely ships lemma + POS frequencies alongside surface counts, but it covers MSA subtitle/newspaper register (160M tokens = 120M movie-subtitle + 40M ARALEX newspaper), with no classical/Quranic coverage and automated (not hand-verified) lemmatization.

**Evidence:** Behavior Research Methods (2024/25, PubMed 40011321) abstract: database includes 'lemmas and part-of-speech information along with their corresponding frequencies' (column vars all_lem_cnt / all_lem_frq), built by 'combining a novel dataset of 120 million word tokens from movie subtitles with 40 million tokens from newspaper articles originally collected in ARALEX' (=160M, MSA scope). Data freely downloadable from OSF node spb8c (public, verified via OSF API — 6 files incl. .RData/.ipynb) plus a query interface at subtlexar.uaeu.ac.ae. Lemmas are from automated morphological tagging (quality/coverage for classical purposes is a separate concern); diglossia means subtitles capture formal MSA, reinforcing the non-classical scoping.

- https://link.springer.com/article/10.3758/s13428-024-02560-8
- https://pubmed.ncbi.nlm.nih.gov/40011321/
- https://osf.io/spb8c/

### [high · 3-0 (constituent claims 0,17,18; plus two REFUTED data-availability claims)]
Buckwalter & Parkinson 'A Frequency Dictionary of Arabic' (Routledge 2011) is high-quality but NOT recommended for integration: it is explicitly modern (5,000 words from a 30M-word MSA+dialects corpus, oldest material 1950s fiction, no Quranic/classical content) AND its underlying word-list data is not available as a downloadable dataset.

**Evidence:** Routledge: 'a list of the 5,000 most frequently used words in Modern Standard Arabic (MSA) as well as several of the most widely spoken Arabic dialects ... Based on a 30-million-word corpus.' The book's own Introduction (preview PDF) details the corpus as 10% spoken/dialect + 90% written across 5 modern genres, 'practically all ... published in 2006-2007', oldest = 1950s fiction; NO Quranic/classical material. Notably it is built on Parkinson's arabiCorpus (arabicorpus.byu.edu), whose FULL holdings include separate Premodern and Qur'an subcorpora that were NOT part of the 30M frequency-dictionary corpus — reinforcing that a Quran track must be computed separately. TWO claims that the former-CD/underlying data is freely downloadable as Routledge 'support material' were REFUTED (0-3 and 1-2): there is no obtainable dataset, only the printed book.

- https://www.routledge.com/9780415444347
- https://api.pageplace.de/preview/DT0400.9781134066612_A24416999

### [high · 3-0 (constituent claims 6,7,16; plus one REFUTED obtainability claim)]
Aralex (Boudelaa & Marslen-Wilson 2010) is an MSA-only lexical database (40M-word contemporary newspaper corpus) offering root/word-pattern token and type/family-size frequencies — i.e. morphological-level stats — but it does NOT explicitly confirm dictionary-lemma keying or homograph disambiguation, and is not the right primary list for either track.

**Evidence:** Titled 'Aralex: A lexical database for Modern Standard Arabic' (Behavior Research Methods 42(2):481-487), 'Based on a contemporary text corpus of 40 million words' (newspaper, e.g. Al-Hayat). Provides 'token frequencies of roots and word patterns', 'type frequency, or family size, of roots and word patterns', and orthographic/root/pattern n-grams — morphological stats, NOT confirmed dictionary-lemma keying or homograph disambiguation. PubMed notes the source is 'modern and standardized Arabic rather than classical, Quranic, or specialized'. The claim that Aralex is freely downloadable under a GNU-like license was REFUTED (1-2) — treat obtainability as uncertain (online query interface exists at the Cambridge MRC-CBU site). Aralex's main value here is as the 40M newspaper component feeding into SUBTLEX-AR.

- https://link.springer.com/article/10.3758/BRM.42.2.481
- https://aralex.mrc-cbu.cam.ac.uk/aralex.online/
- https://pubmed.ncbi.nlm.nih.gov/20479179/

## Refuted (did NOT survive verification)

- (0-3) The dictionary's underlying data (former CD content) is publicly available as downloadable 'support material' at www.routledge.com/9780415444347, provided as full text in a format that researchers can process into custom frequency lists.
- (1-2) Aralex is freely obtainable: distributed under a GNU-like license, queryable online or downloadable from the MRC-CBU Cambridge server.
- (1-2) QuranMorph's lemmatization explicitly disambiguates homographs by context, e.g. the verb ضرب (daraba) is assigned different meanings/lemma senses depending on the verse — 'to provide an example' in Quran 14:24 vs 'to travel/journey' in Quran 4:101.
- (1-2) QuranMorph is open-source and publicly available for free download under Creative Commons Attribution 4.0 (CC-BY-4.0) from the SinaLab resources page at https://sina.birzeit.edu/quran, produced by Birzeit University.
- (1-2) The former CD content — i.e. the underlying full word-list data — is now freely available online at routledge.com/9780415444347 as support material in a format designed for corpus/computational linguists to process into their own lists.

## Open questions / gaps

- What is the actual measured homograph-disambiguation accuracy of SUBTLEX-AR's and Aralex's automated lemmatizers (e.g. how often does أمر noun vs verb get split correctly)? Sources confirm lemma columns exist but not their precision.
- Is QuranMorph released under a redistribution-friendly license (the CC-BY-4.0 claim was refuted), and does its download bundle include a ready frequency table or only per-token annotations requiring you to compute counts?
- Were arTenTen (SketchEngine), KALIMAT, the Leeds Arabic Internet Corpus standalone, and OSIAN ever vetted for lemmatized frequency exports? They were named in the question but no surviving claims address their lemma quality, classical coverage, or download/API access.
- Can a high-quality classical/medieval-literary frequency track (beyond Quran) be built from an open premodern corpus (e.g. arabiCorpus Premodern subcorpus, OpenITI), and is that data extractable as lemmatized counts — the question's classical-literature goal is only partly served by a Quran-only track?