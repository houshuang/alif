"""Roma Aeterna .apkg parser: HTML stripper preserves sense separators.

Anki notes for the Roma Aeterna deck wrap each English sense in block-level
tags (e.g. `<div>demolition</div><div>setting of the sun</div>`). The old
parser collapsed all tags to a single space, producing nonsense run-on
phrases like `"demolition setting of the sun"` for excidium/exordium that
then surfaced on the user's lookup card as a 3-word comma-less "translation"
(2026-05-26 audit). These tests pin the block-tag → "; " behavior so any
future regression on _clean() is caught at test time.
"""
from scripts.parse_roma_aeterna_apkg import _clean


def test_block_div_senses_separated_with_semicolons():
    raw = "<div>demolition</div><div>setting of the sun</div>"
    assert _clean(raw) == "demolition; setting of the sun"


def test_consecutive_block_tags_collapse_to_single_separator():
    raw = "<div>beginning</div></div><div>introduction</div><div>foundation</div>"
    assert _clean(raw) == "beginning; introduction; foundation"


def test_inline_tags_still_become_space():
    raw = "an <i>assistant</i> or <i>attendant</i>"
    assert _clean(raw) == "an assistant or attendant"


def test_br_treated_as_sense_separator():
    raw = "copper<br/>bronze"
    assert _clean(raw) == "copper; bronze"


def test_list_items_become_senses():
    raw = "<ul><li>strict</li><li>paltry</li><li>inadequate</li></ul>"
    assert _clean(raw) == "strict; paltry; inadequate"


def test_table_cells_become_senses():
    raw = "<tr><td>animal</td><td>for drawing a plow</td></tr>"
    assert _clean(raw) == "animal; for drawing a plow"


def test_no_leading_or_trailing_separator():
    raw = "<div><div>solo sense</div></div>"
    assert _clean(raw) == "solo sense"


def test_html_entities_unescaped():
    raw = "<div>fair&nbsp;weather</div><div>good&amp;clear</div>"
    assert _clean(raw) == "fair weather; good&clear"


def test_single_sense_no_block_tags_unchanged():
    raw = "<span>king</span>"
    assert _clean(raw) == "king"


def test_empty_block_tags_dropped():
    raw = "<div></div><div>only sense</div><div></div>"
    assert _clean(raw) == "only sense"


def test_whitespace_collapsed_inside_sense():
    raw = "<div>front     part    of\na vessel</div>"
    assert _clean(raw) == "front part of a vessel"


def test_macrons_preserved():
    # Parser leaves macrons; the importer strips them later via NFKD.
    raw = "<div>strāmentum</div><div>straw</div>"
    assert _clean(raw) == "strāmentum; straw"
