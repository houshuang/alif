#!/usr/bin/env python3
"""Map sim checkpoints onto Momo sample coverage.

coverage(K) = (function + sum(mapped[l] for l in K)) / total
where K = known∪learning lemma ids (base) + sim-created bare-form matches
against the unmapped token bucket.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

DIA = re.compile(r"[ً-ْٰـ]")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "")
    s = DIA.sub("", s)
    return (s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
             .replace("ٱ", "ا"))


def coverage(tokenmap: dict, base_ids: set[int], new_bares: set[str]) -> float:
    mapped = tokenmap["mapped"]
    cov_tokens = tokenmap["function"]
    cov_tokens += sum(c for lid, c in mapped.items() if int(lid) in base_ids)
    unm = tokenmap["unmapped_freq"]
    norm_new = {norm(b) for b in new_bares}
    cov_tokens += sum(c for surf, c in unm.items() if norm(surf) in norm_new)
    return cov_tokens / tokenmap["total"] * 100


def main():
    tokenmap = json.loads(Path(sys.argv[1]).read_text())
    print(f"{'variant':<18} {'day':>4} {'known':>6} {'cov_known%':>10} {'cov+learn%':>10} {'cov+acq%':>9}")
    for run_file in sys.argv[2:]:
        run = json.loads(Path(run_file).read_text())
        v = run["variant"]
        name = f"v{v['volume']}"
        if v["schedule"] != "all":
            name += f"_{v['schedule']}"
        if v["break"][0]:
            name += "_break"
        for cp in run["checkpoints"]:
            known = set(cp["known_ids"])
            knownb = set(cp.get("known_new_bares", []))
            learn = known | set(cp["learning_ids"])
            learnb = knownb | set(cp.get("learning_new_bares", []))
            acq = learn | set(cp["acquiring_ids"])
            acqb = learnb | set(cp.get("acquiring_new_bares", []))
            n_known = cp["counts"].get("known", 0)
            print(f"{name:<18} {cp['day']:>4} {n_known:>6} "
                  f"{coverage(tokenmap, known, knownb):>10.1f} "
                  f"{coverage(tokenmap, learn, learnb):>10.1f} "
                  f"{coverage(tokenmap, acq, acqb):>9.1f}")


if __name__ == "__main__":
    main()
