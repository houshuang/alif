import pytest

from scripts.import_reader_book import normalize_artifact


def test_normalizes_explicit_pages():
    metadata, pages = normalize_artifact({
        "title_ar": "كتاب",
        "pages": [{
            "arabic": "الأولى",
            "english": "First",
            "source_page_number": 67,
            "pdf_page_number": 68,
        }],
    })
    assert metadata["title_ar"] == "كتاب"
    assert pages == [{
        "arabic": "الأولى",
        "english": "First",
        "source_page_number": 67,
        "pdf_page_number": 68,
    }]


def test_normalizes_bookify_paragraphs():
    metadata, pages = normalize_artifact({
        "title": "كليلة ودمنة",
        "author": "ابن المقفع",
        "paragraphs": [{"ar": "نص.", "en": "Text."}],
    })
    assert metadata == {"title_ar": "كليلة ودمنة", "author": "ابن المقفع"}
    assert pages == [{"arabic": "نص.", "english": "Text."}]


def test_normalizes_bookifier_cache_in_insertion_order():
    _metadata, pages = normalize_artifact({
        "hash-1": {"src": "الأولى", "ar": "الأُولَى", "en": "First"},
        "hash-2": {"src": "الثانية", "ar": "الثَّانِيَة", "en": "Second"},
    })
    assert [page["arabic"] for page in pages] == ["الأُولَى", "الثَّانِيَة"]


def test_rejects_unknown_artifact_shape():
    with pytest.raises(ValueError, match="Unrecognized artifact shape"):
        normalize_artifact({"content": "not a supported export"})
