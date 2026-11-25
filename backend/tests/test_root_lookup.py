from app.models import Root
from app.services.morphology import clean_root_string, find_or_create_root, root_spellings


def test_clean_root_string_normalizes_initial_hamza_and_maqsura():
    assert clean_root_string("ا.م.ن") == "أ.م.ن"
    assert clean_root_string("ء.ك.د") == "أ.ك.د"
    assert clean_root_string("ه.د.ى") == "ه.د.ي"
    assert clean_root_string("كَ.تَ.بَ") == "ك.ت.ب"
    assert clean_root_string("ك.ت") is None


def test_root_spellings_cover_every_hamza_seat():
    spellings = root_spellings("ر.أ.س")
    assert {"ر.أ.س", "ر.ئ.س", "ر.ء.س", "ر.ا.س"} <= set(spellings)
    assert root_spellings("ك.ت.ب") == ["ك.ت.ب"]


def test_reuses_existing_root_written_with_another_hamza_seat(db_session):
    stored = Root(root="ر.أ.س")
    db_session.add(stored)
    db_session.flush()

    assert find_or_create_root(db_session, "ر.ئ.س").root_id == stored.root_id
    assert find_or_create_root(db_session, "ر.ء.س").root_id == stored.root_id
    assert db_session.query(Root).count() == 1


def test_initial_plain_alif_finds_hamza_root(db_session):
    stored = Root(root="أ.م.ن")
    db_session.add(stored)
    db_session.flush()

    assert find_or_create_root(db_session, "ا.م.ن").root_id == stored.root_id


def test_prefers_exact_spelling_when_duplicates_still_exist(db_session):
    db_session.add_all([Root(root="أ.ك.د"), Root(root="ء.ك.د")])
    db_session.flush()

    assert find_or_create_root(db_session, "ء.ك.د").root == "أ.ك.د"


def test_creates_new_root_once(db_session):
    first = find_or_create_root(db_session, "ق.ر.أ")
    second = find_or_create_root(db_session, "ق.ر.ء")
    assert first is not None and first.root == "ق.ر.أ"
    assert second.root_id == first.root_id


def test_refuses_to_create_root_with_plain_alif_radical(db_session):
    assert find_or_create_root(db_session, "ج.ا.ز") is None
    assert db_session.query(Root).count() == 0


def test_invalid_root_returns_none(db_session):
    assert find_or_create_root(db_session, None) is None
    assert find_or_create_root(db_session, "abc") is None
