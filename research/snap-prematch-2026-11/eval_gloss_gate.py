import json, os, re, sys, time, collections
S = os.path.dirname(os.path.abspath(__file__))
from app.services.gloss_gate import review_widenings
w = json.load(open(f"{S}/snap_prematch_widened.json"))
lab = {int(k): v for k, v in json.load(open(f"{S}/fable_labels.json")).items()}
pages = {p["page_id"]: p["arabic_text"] for p in json.load(open(f"{S}/snap_prematch_export.json"))}
import sqlite3
db = sqlite3.connect(os.environ["DATABASE_URL"].replace("sqlite:///", ""))
items = []
for i, (pid, surface, lid, frm, to) in enumerate(w, 1):
    text = pages.get(pid, "")
    sent = next((s for s in re.split(r"(?<=[.!؟?])\s+|\n", text) if surface in s), "")
    ar, pos = db.execute("select lemma_ar, pos from lemmas where lemma_id=?", (lid,)).fetchone()
    items.append({"index": i, "lemma_id": lid, "lemma_ar": ar, "pos": pos, "old_gloss": frm,
                  "new_gloss": to, "surface": surface, "context": sent})
t = time.time()
out = review_widenings(items)
print(f"{time.time()-t:.0f}s")
tab = collections.Counter((lab[i], out[i]["approved"]) for i in out)
for l in ("good", "noop", "doubtful", "bad"):
    print(f"{l:9} approved {tab[(l, True)]:3}  refused {tab[(l, False)]:3}")
json.dump({i: {**out[i], "label": lab[i], **items[i-1]} for i in out}, open(f"{S}/gate_eval.json", "w"), ensure_ascii=False, indent=1)
for i in out:
    if (lab[i] in ("bad", "doubtful") and out[i]["approved"]) or (lab[i] == "good" and not out[i]["approved"]):
        it = items[i-1]
        print(f"#{i} [{lab[i]}→{'APPROVED' if out[i]['approved'] else 'refused:'+out[i]['reason']}] {it['lemma_ar']} | {it['old_gloss']} → {it['new_gloss']} | {out[i].get('note','')[:110]}")
