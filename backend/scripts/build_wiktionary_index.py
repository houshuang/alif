"""Build the compact Wiktionary sense index used as dictionary evidence by the
gloss-widening gate (services/gloss_gate.py).

Reads the kaikki.org Arabic extract that scripts/import_wiktionary.py caches
(data/wiktionary_arabic.jsonl.gz) and writes data/wiktionary_index.json.gz:
{bare headword: [[vocalized headword, pos, [gloss, ...]], ...]}. Names are
left out; glosses are cut to 8 per entry.

    python3 scripts/build_wiktionary_index.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.gloss_gate import INDEX_PATH, bare_key  # noqa: E402

SOURCE = Path(__file__).resolve().parents[1] / "data" / "wiktionary_arabic.jsonl.gz"
_SKIP_POS = {"name", "character", "symbol", "romanization", "suffix", "prefix", "infix"}


def main() -> None:
    index: dict[str, list] = {}
    entries = 0
    with gzip.open(SOURCE, "rt", encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            pos = d.get("pos") or ""
            if pos in _SKIP_POS:
                continue
            glosses = []
            for sense in d.get("senses") or []:
                for g in sense.get("glosses") or []:
                    g = g.strip()
                    if g and g not in glosses:
                        glosses.append(g)
            if not glosses:
                continue
            heads = [f["form"] for f in d.get("forms") or [] if "canonical" in (f.get("tags") or [])]
            head = heads[0] if heads else d.get("word") or ""
            key = bare_key(head or d.get("word") or "")
            if not key:
                continue
            index.setdefault(key, []).append([head, pos, glosses[:8]])
            entries += 1
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(INDEX_PATH, "wt", encoding="utf-8") as out:
        json.dump(index, out, ensure_ascii=False)
    print(f"{entries} entries under {len(index)} headwords -> {INDEX_PATH}")


if __name__ == "__main__":
    main()
