"""Regression tests for the two import paths fixed in Phase 2 of the
lemma-decomposition audit (research/decomposition-audit-2026-04-24.md):

1. quran_service._create_unknown_quran_lemmas — was using exact-string set
   membership, would create compound lemmas (e.g. وَتَرَكَهُم) instead of
   mapping to existing canonical (تَرَكَ).
2. backfill_function_word_lemmas.backfill_function_words — same pattern,
   lower blast radius (curated input).

Both paths now call resolve_existing_lemma() before creating, matching the
reference pattern in story_service.py:305, 348, 508.
"""
from unittest.mock import patch

from app.models import Lemma
from app.services.quran_service import _create_unknown_quran_lemmas


class TestQuranLemmaCreateDedup:
    def test_dedup_via_clitic_strip(self, db_session):
        """LLM returns the compound bare (وتركهم); clitic-strip must find canonical ترك."""
        canonical = Lemma(
            lemma_ar="تَرَكَ", lemma_ar_bare="ترك",
            gloss_en="to leave", pos="verb", source="seed",
        )
        db_session.add(canonical)
        db_session.commit()
        canonical_id = canonical.lemma_id

        all_lemmas = db_session.query(Lemma).all()

        with patch("app.services.llm.generate_completion") as mock_llm:
            mock_llm.return_value = [{
                "bare": "وتركهم",
                "gloss_en": "and left them",
                "pos": "verb",
                "is_name": False,
                "root": None,
            }]
            result_map = _create_unknown_quran_lemmas(
                db_session,
                unknown_forms={"وتركهم": "وَتَرَكَهُم"},
                all_lemmas=all_lemmas,
            )

        assert result_map.get("وتركهم") == canonical_id
        assert db_session.query(Lemma).count() == 1

    def test_dedup_via_direct_match(self, db_session):
        """LLM returns the canonical bare directly; lookup must reuse, not duplicate."""
        canonical = Lemma(
            lemma_ar="لَكِنْ", lemma_ar_bare="لكن",
            gloss_en="but", pos="particle", source="seed",
        )
        db_session.add(canonical)
        db_session.commit()
        canonical_id = canonical.lemma_id

        all_lemmas = db_session.query(Lemma).all()

        with patch("app.services.llm.generate_completion") as mock_llm:
            mock_llm.return_value = [{
                "bare": "لكن",
                "gloss_en": "but",
                "pos": "particle",
                "is_name": False,
                "root": None,
            }]
            result_map = _create_unknown_quran_lemmas(
                db_session,
                unknown_forms={"لكن": "لَكِنْ"},
                all_lemmas=all_lemmas,
            )

        assert result_map.get("لكن") == canonical_id
        assert db_session.query(Lemma).count() == 1

    def test_creates_when_truly_new(self, db_session):
        """Sanity check: if no canonical exists and bare doesn't clitic-strip
        to anything, a new lemma is created."""
        all_lemmas = db_session.query(Lemma).all()

        with patch("app.services.llm.generate_completion") as mock_llm:
            mock_llm.return_value = [{
                "bare": "زمزم",
                "gloss_en": "Zamzam (well)",
                "pos": "noun",
                "is_name": True,
                "root": None,
            }]
            with patch("app.services.lemma_quality.run_quality_gates"):
                result_map = _create_unknown_quran_lemmas(
                    db_session,
                    unknown_forms={"زمزم": "زَمْزَم"},
                    all_lemmas=all_lemmas,
                )

        assert result_map.get("زمزم") is not None
        assert db_session.query(Lemma).count() == 1

    def test_inflected_verb_creates_canonical_lemma(self, db_session):
        """Conjugated verb form (نَزَّلْنَا "we sent down") must create the
        canonical lemma at the citation form (نَزَّلَ), not at the inflected
        surface.

        Regression for 2026-05-15 user bug: نَزَّلْنَا was being introduced as
        a textbook intro card with its own conjugation table. Before the fix,
        Phase 2 LLM prompt asked for "bare as given" so the inflected form
        leaked through. After the fix, CAMeL Tools canonicalizes upstream and
        the LLM only sees the lex.
        """
        all_lemmas = db_session.query(Lemma).all()

        with patch(
            "app.services.morphology.get_best_lemma_mle"
        ) as mock_mle, patch(
            "app.services.morphology.CAMEL_AVAILABLE", True
        ), patch(
            "app.services.llm.generate_completion"
        ) as mock_llm, patch(
            "app.services.lemma_quality.run_quality_gates"
        ):
            mock_mle.return_value = {
                "lex": "نَزَّلَ",
                "root": "ن.ز.ل",
                "pos": "verb",
                "enc0": "",
            }
            mock_llm.return_value = [{
                "bare": "نزل",
                "gloss_en": "to send down",
                "pos": "verb",
                "is_name": False,
                "root": "ن.ز.ل",
            }]

            result_map = _create_unknown_quran_lemmas(
                db_session,
                unknown_forms={"نزلنا": "نَزَّلْنَا"},
                all_lemmas=all_lemmas,
            )

        assert db_session.query(Lemma).count() == 1
        created = db_session.query(Lemma).first()
        # Canonical form, not the inflected surface
        assert created.lemma_ar_bare == "نزل"
        assert created.lemma_ar == "نَزَّلَ"
        # The infinitive gloss, not "we sent down"
        assert created.gloss_en == "to send down"
        # Result map still keyed by the original (inflected) bare for caller
        assert result_map.get("نزلنا") == created.lemma_id

    def test_multiple_inflected_forms_share_canonical(self, db_session):
        """Two surfaces from the same root (نَزَّلْنَا and نَزَّلْتُ) must
        collapse to a single canonical Lemma, not produce two."""
        all_lemmas = db_session.query(Lemma).all()

        mle_by_word = {
            "نَزَّلْنَا": {"lex": "نَزَّلَ", "root": "ن.ز.ل", "pos": "verb", "enc0": ""},
            "نَزَّلْتُ": {"lex": "نَزَّلَ", "root": "ن.ز.ل", "pos": "verb", "enc0": ""},
        }

        def mock_mle(word):
            return mle_by_word.get(word)

        with patch(
            "app.services.morphology.get_best_lemma_mle", side_effect=mock_mle
        ), patch(
            "app.services.morphology.CAMEL_AVAILABLE", True
        ), patch(
            "app.services.llm.generate_completion"
        ) as mock_llm, patch(
            "app.services.lemma_quality.run_quality_gates"
        ):
            mock_llm.return_value = [{
                "bare": "نزل",
                "gloss_en": "to send down",
                "pos": "verb",
                "is_name": False,
                "root": "ن.ز.ل",
            }]

            result_map = _create_unknown_quran_lemmas(
                db_session,
                unknown_forms={
                    "نزلنا": "نَزَّلْنَا",
                    "نزلت": "نَزَّلْتُ",
                },
                all_lemmas=all_lemmas,
            )

        # Single canonical Lemma row despite two distinct surfaces
        assert db_session.query(Lemma).count() == 1
        canonical = db_session.query(Lemma).first()
        assert canonical.lemma_ar_bare == "نزل"
        # Both surfaces point at the canonical
        assert result_map.get("نزلنا") == canonical.lemma_id
        assert result_map.get("نزلت") == canonical.lemma_id

    def test_camel_canonical_already_in_db_just_links(self, db_session):
        """If the canonical (نَزَّلَ) is already a Lemma, the inflected
        surface (نَزَّلْنَا) must link to it — not create a duplicate."""
        canonical = Lemma(
            lemma_ar="نَزَّلَ", lemma_ar_bare="نزل",
            gloss_en="to send down", pos="verb", source="seed",
        )
        db_session.add(canonical)
        db_session.commit()
        canonical_id = canonical.lemma_id

        all_lemmas = db_session.query(Lemma).all()

        with patch(
            "app.services.morphology.get_best_lemma_mle"
        ) as mock_mle, patch(
            "app.services.morphology.CAMEL_AVAILABLE", True
        ), patch(
            "app.services.llm.generate_completion"
        ) as mock_llm:
            mock_mle.return_value = {
                "lex": "نَزَّلَ", "root": "ن.ز.ل", "pos": "verb", "enc0": "",
            }
            result_map = _create_unknown_quran_lemmas(
                db_session,
                unknown_forms={"نزلنا": "نَزَّلْنَا"},
                all_lemmas=all_lemmas,
            )

            # LLM must not have been called — canonical already in DB
            mock_llm.assert_not_called()

        assert result_map.get("نزلنا") == canonical_id
        assert db_session.query(Lemma).count() == 1


class TestBackfillFunctionWordsDedup:
    def test_dedup_via_clitic_strip(self, db_session, monkeypatch):
        """Compound entry (و + لكن) in the glosses dict must clitic-strip to
        existing canonical, not create a new lemma."""
        canonical = Lemma(
            lemma_ar="لكن", lemma_ar_bare="لكن",
            gloss_en="but", pos="particle", source="seed",
        )
        db_session.add(canonical)
        db_session.commit()

        from scripts import backfill_function_word_lemmas as bfl
        monkeypatch.setattr(bfl, "FUNCTION_WORD_GLOSSES", {"ولكن": "and-but"})

        created, skipped_existing, _ = bfl.backfill_function_words(db_session, verbose=False)

        assert created == 0
        assert skipped_existing == 1
        assert db_session.query(Lemma).count() == 1

    def test_dedup_via_direct_match(self, db_session, monkeypatch):
        """Direct exact-bare match must skip — basic dedup regression."""
        canonical = Lemma(
            lemma_ar="في", lemma_ar_bare="في",
            gloss_en="in", pos="particle", source="seed",
        )
        db_session.add(canonical)
        db_session.commit()

        from scripts import backfill_function_word_lemmas as bfl
        monkeypatch.setattr(bfl, "FUNCTION_WORD_GLOSSES", {"في": "in"})

        created, skipped_existing, _ = bfl.backfill_function_words(db_session, verbose=False)

        assert created == 0
        assert skipped_existing == 1
        assert db_session.query(Lemma).count() == 1

    def test_creates_when_new(self, db_session, monkeypatch):
        """Sanity: a brand-new function word with no canonical match is created."""
        from scripts import backfill_function_word_lemmas as bfl
        monkeypatch.setattr(bfl, "FUNCTION_WORD_GLOSSES", {"غير": "other than"})

        created, _, _ = bfl.backfill_function_words(db_session, verbose=False)

        assert created == 1
        assert db_session.query(Lemma).count() == 1
