"""Timing is observation only; it must never alter scheduling or product ratings."""
from pydantic import ValidationError
from app.schemas import ReadingTimingIn


def normalize_reading_timing(raw, *, review_mode, comprehension_signal,
                            sentence_ids, missed_ids, confused_ids, word_evidence,
                            expected_token_ids, lookup_count, audio_play_count, parent_card_type):
    if raw is None:
        return None  # Legacy client: absence is not a clean measurement.
    try:
        timing = ReadingTimingIn.model_validate(raw).model_dump()
    except (ValidationError, TypeError):
        return {"version": 1, "elapsed_ms": None, "exclusions": ["invalid_payload"], "eligible": False}
    reasons = set(timing["exclusions"])
    if review_mode != "reading" or len(sentence_ids) != 1 or parent_card_type != "sentence":
        reasons.add("not_single_reading_sentence")
    if comprehension_signal != "understood" or missed_ids or confused_ids:
        reasons.add("marked_word")
    if not word_evidence or {e["sentence_word_id"] for e in word_evidence} != set(expected_token_ids):
        reasons.add("missing_token_evidence")
    if lookup_count != 0 or audio_play_count != 0:
        reasons.add("assistance_or_unknown")
    for e in word_evidence or []:
        if e.get("rating") != 3 or e.get("causes"):
            reasons.add("marked_word")
        if e.get("front_toggle_count", 0):
            reasons.add("assistance")
    if timing["elapsed_ms"] is None or timing["elapsed_ms"] < 1:
        reasons.add("not_started")
    timing["exclusions"] = sorted(reasons)
    timing["eligible"] = not reasons
    return timing
