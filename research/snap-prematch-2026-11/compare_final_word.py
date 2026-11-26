"""Compare the word each method ends on: the per-tap check vs. the page pass.

SAME = both end on the same vocalized citation form. GAP = same word, but the
tap widened the gloss and the page pass found the stored gloss sufficient."""
import json,sys,re,os,sqlite3
from app.services.preread_service import _vocalized_key as vk
DB=sqlite3.connect(os.environ["ALIF_DB"])
ar=lambda i:(DB.execute("select lemma_ar from lemmas where lemma_id=?",(i,)).fetchone() or [None])[0]
norm=lambda s: re.sub(r"[ً-ْـ]","",s or "")
strip=lambda s: re.sub(r"[^ء-يً-ْـ]","",s or "")
tot={"same_word":0,"diff_word":0,"gloss_gap":0,"n":0}
for path in sys.argv[1:]:
  for r in json.load(open(path)):
    by={}
    for w in r["words"]:
      for f in w.get("surface_forms") or [w["surface"]]: by.setdefault(norm(f),w)
    for d in r["taps"]:
      if d["event"]!="discover_lookup_sense" or not d.get("verdict"): continue
      w=by.get(norm(strip(d["surface"])))
      tap_final=d.get("word_lemma_ar") if d["verdict"]=="different" else d["lemma_ar"]
      bv=w.get("form_verdicts",{}).get(next((x for x in w["surface_forms"] if norm(x)==norm(strip(d["surface"]))),None)) or w["batch_verdict"]
      if bv["verdict"]=="sibling": pre_final=ar(bv["lemma_id"])
      elif bv["verdict"]=="different": pre_final=bv["lemma_ar"]
      else: pre_final=w["lemma_ar"]
      same = vk(tap_final or "")==vk(pre_final or "")
      tot["n"]+=1
      gap = same and d["verdict"]=="broader" and bv["verdict"]=="fits" and w["lemma_id"]==d["lemma_id"]
      tot["same_word" if same else "diff_word"]+=1; tot["gloss_gap"]+=gap
      print("SAME" if same else "DIFF", "GAP" if gap else "   ", d["surface"], "| tap:",tap_final,d["verdict"],(d.get("word_gloss") or d.get("gloss_to") or d.get("gloss_from") or "")[:30],"|| pre:",w["lemma_ar"],"->",bv["verdict"],pre_final,(bv.get("gloss_en") or w.get("gloss_en") or "")[:30])
print(tot)
