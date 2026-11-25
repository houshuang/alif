import os, sys, json
from collections import Counter
sys.path.insert(0, "/Users/stian/src/alif/backend")
from app.database import engine, SessionLocal
from app.models import Lemma
print("ENGINE", engine.url)
db = SessionLocal()
lem = db.query(Lemma).all()
print("lemmas", len(lem), "canonical(non-variant)", sum(1 for l in lem if l.canonical_lemma_id is None))
print("pos", Counter(l.pos for l in lem).most_common(20))
keys = Counter(); keys_verb = Counter(); keys_nonverb=Counter()
for l in lem:
    f = l.forms_json
    if isinstance(f, dict):
        for k, v in f.items():
            if isinstance(v, str) and v.strip():
                keys[k]+=1
                (keys_verb if l.pos in ("verb",) else keys_nonverb)[k]+=1
print("all keys", keys.most_common())
print("verb keys", keys_verb.most_common())
print("nonverb keys", keys_nonverb.most_common(30))
verbs=[l for l in lem if l.pos=="verb"]
print("verbs", len(verbs), "non-variant", sum(1 for l in verbs if l.canonical_lemma_id is None), "with forms", sum(1 for l in verbs if isinstance(l.forms_json, dict) and l.forms_json))
