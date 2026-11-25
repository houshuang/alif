"""Per book token: lemma the resolver credits vs CAMeL's own lemma. Read-only."""
import json, os, sys, pickle
from collections import Counter, defaultdict
from pathlib import Path
os.environ.setdefault("ALIF_SKIP_MIGRATIONS","1")
W=Path("/Users/stian/src/alif/.claude/worktrees/frequent-intake/backend")
sys.path.insert(0,str(W)); sys.path.insert(0,str(W/"scripts"))
import build_book_frequency as B
from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.morphology import get_best_lemma_mle
from app.services.canonical_resolution import resolve_canonical_via_map
db=SessionLocal()
corpus=Path("/Users/stian/src/alif/backend/data/corpora/barec_sentences.json")
texts=[r["text"] for r in json.loads(corpus.read_text()) if r.get("source") in set(B.DEFAULT_SOURCES)]
lookup=B.build_comprehensive_lemma_lookup(db)
L={l.lemma_id:l for l in db.query(Lemma).all()}
parents={i:l.canonical_lemma_id for i,l in L.items()}
state={u.lemma_id:u.knowledge_state for u in db.query(UserLemmaKnowledge).all()}
def nk(s): return B.normalize_alef(B.strip_tatweel(B.strip_diacritics(s or ""))).replace("ى","ي").replace("ٱ","ا")
cache={}; tokc=Counter()
for t in texts:
    for tok in B.tokenize(t):
        if B.norm_bare(tok): tokc[tok]+=1
groups=defaultdict(lambda: {"n":0,"surf":Counter(),"credited":Counter()})
per_lemma=defaultdict(lambda: {"n":0,"agree":0,"other":Counter()})
for tok,n in tokc.items():
    bare=B.norm_bare(tok)
    if not B.requires_exact_running_text_alias(tok) and B.is_function_surface(bare): continue
    lid=B.lookup_lemma_id(tok, lookup, strict_fallback=True)
    if lid is not None: lid=resolve_canonical_via_map(lid, parents)
    lem=L.get(lid) if lid else None
    if lem is not None and (lem.word_category in {"proper_name","onomatopoeia"} or lem.pos=="particle" or lem.function_word_override or B._is_function_word(B.normalize_alef(B.strip_diacritics(lem.lemma_ar_bare or "")))):
        continue
    try: a=get_best_lemma_mle(tok)
    except Exception: a=None
    lex=(a or {}).get("lex"); pos=((a or {}).get("pos") or "").lower()
    if lem is not None:
        d=per_lemma[lid]; d["n"]+=n
        if not lex or nk(lex)==nk(lem.lemma_ar_bare): d["agree"]+=n; continue
        d["other"][(lex,pos)]+=n
    if not lex or pos=="noun_prop" or not pos.startswith(B.CONTENT_POS): continue
    g=groups[(lex,pos.split("_")[0])]; g["n"]+=n; g["surf"][tok]+=n; g["credited"][lid]+=n
out=open(S:=sys.argv[1],"w")
for (lex,pos),g in sorted(groups.items(), key=lambda kv:-kv[1]["n"]):
    if g["n"]<int(sys.argv[2]): break
    cred="; ".join(f"{n}→"+(f"{L[i].lemma_ar}({L[i].pos},{(L[i].gloss_en or '')[:20]})" if i else "NONE") for i,n in g["credited"].most_common(2))
    out.write(f"{g['n']}\t{lex}\t{pos}\t{' '.join(f'{s}={k}' for s,k in g['surf'].most_common(4))}\t{cred}\n")
out.close()
out=open(sys.argv[3],"w")
for lid,d in sorted(per_lemma.items(), key=lambda kv:-kv[1]["n"]):
    if d["n"]<10: break
    l=L[lid]
    out.write(f"{d['n']}\t{d['agree']}\t{lid}\t{l.lemma_ar}\t{l.pos}\t{(l.gloss_en or '')[:30]}\t{state.get(lid)}\t{' '.join(f'{x[0]}/{x[1]}={k}' for x,k in d['other'].most_common(4))}\n")
