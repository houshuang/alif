"""Morphology analysis service - placeholder returning mock data.

Real CAMeL Tools integration will replace this later.
"""


def analyze_word(word: str) -> dict:
    return {
        "word": word,
        "lemma": word,
        "root": None,
        "pos": "UNK",
        "gloss_en": None,
        "source": "mock",
    }


def analyze_sentence(sentence: str) -> dict:
    words = sentence.split()
    return {
        "sentence": sentence,
        "words": [analyze_word(w) for w in words],
        "source": "mock",
    }
