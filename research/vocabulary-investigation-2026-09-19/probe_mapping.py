"""Read-only selector, QAC identity, and Momo mapping diagnostics.

Momo is an automatic mapping audit, NOT verified comprehension/coverage.
Unmapped tokens stay unresolved, never presumed readable. No LLM calls.
"""
import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
os.environ['ALIF_SKIP_MIGRATIONS']='1'
os.environ['DATABASE_URL']='sqlite:///:memory:'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import Lemma,UserLemmaKnowledge
from app.services.word_selector import select_next_words
from app.services.quran_frequency import map_quran_frequencies
from app.services.sentence_validator import build_comprehensive_lemma_lookup,map_tokens_to_lemmas,tokenize,is_function_word_lemma

ap=argparse.ArgumentParser()
ap.add_argument('--db',type=Path,required=True)
ap.add_argument('--text',type=Path,required=True)
ap.add_argument('--qac',type=Path,required=True)
ap.add_argument('--out',type=Path,required=True)
a=ap.parse_args()
before=hashlib.sha256(a.db.read_bytes()).hexdigest()
def connect():
    c=sqlite3.connect(f'file:{a.db.resolve()}?mode=ro&immutable=1',uri=True)
    c.execute('pragma query_only=on')
    return c
engine=create_engine('sqlite://',creator=connect)
with Session(engine,autoflush=False) as db:
    lemmas={l.lemma_id:l for l in db.query(Lemma).all()}
    ulks={u.lemma_id:u for u in db.query(UserLemmaKnowledge).all()}
    def canonical(i):
        seen=set()
        while i in lemmas and lemmas[i].canonical_lemma_id and i not in seen:
            seen.add(i);i=lemmas[i].canonical_lemma_id
        return i
    candidates=select_next_words(db,count=40)
    candidate_output=[{k:r.get(k) for k in ('lemma_id','lemma_ar','gloss_en','frequency_rank','frequency_core_rank','score','score_breakdown')} for r in candidates]
    lookup=build_comprehensive_lemma_lookup(db)
    ranked,qac=map_quran_frequencies(db,lemma_lookup=lookup,lemmas_by_id=lemmas,path=a.qac,collect_report=True)
    suspects=[r for r in qac['mapped'] if r['lemma_id'] in (751,1436,2023,1267,1224)]
    # Include all direct POS mismatches for audit, without asserting every mismatch is wrong.
    from app.services.quran_frequency import pos_match
    mismatches=[r for r in qac['mapped'] if r['pos'] in ('N','V','ADJ','ADV') and r['alif_pos'] and not pos_match(r['pos'],r['alif_pos'])]
    text=a.text.read_text();tokens=tokenize(text)
    # Shared mapping is token-local at this stage; unique surface cache preserves semantics.
    unique=list(dict.fromkeys(tokens))
    mappings=map_tokens_to_lemmas(unique,lookup,None,None)
    mapped={m.surface_form:m for m in mappings}
    counts=Counter();word_counts=Counter();ambiguous_counts=Counter();unmapped=Counter();forms=defaultdict(Counter)
    for t in tokens:
        m=mapped.get(t)
        if not m:continue
        counts['tokens']+=1;i=canonical(m.lemma_id)
        if i is None or i not in lemmas:
            counts['unmapped']+=1;unmapped[t]+=1;continue
        l=lemmas[i]
        if m.alternative_lemma_ids:
            counts['ambiguous_tokens']+=1;ambiguous_counts[i]+=1
        if l.word_category in ('proper_name','onomatopoeia'):
            counts['inert']+=1;continue
        if is_function_word_lemma(l.lemma_ar_bare,l.function_word_override):
            counts['function']+=1;continue
        counts['content_mapped']+=1;word_counts[i]+=1;forms[i][t]+=1
        state=ulks[i].knowledge_state if i in ulks else 'no_state'
        counts[state]+=1
    table=[]
    for i,n in word_counts.most_common():
        l=lemmas[i];u=ulks.get(i)
        table.append({'lemma_id':i,'arabic':l.lemma_ar,'gloss':l.gloss_en,'count':n,'ambiguous_count':ambiguous_counts[i],
                      'state':u.knowledge_state if u else 'no_state','source':u.source if u else None,
                      'surface_examples':forms[i].most_common(3)})
    out={'code_base':'c2fedbe2fbfd4974649fd48571b6795f29b7a453','database_sha256':before,
         'text_sha256':hashlib.sha256(a.text.read_bytes()).hexdigest(),'qac_sha256':hashlib.sha256(a.qac.read_bytes()).hexdigest(),
         'selector_candidates':candidate_output,'qac_suspect_mappings':suspects,'qac_pos_mismatches':mismatches,
         'momo':{'counts':dict(counts),'words':table,'unmapped_top100':unmapped.most_common(100),
                 'limitation':'Token-local shared resolver; not context-verified. Counts can still attach to wrong senses. Unmapped tokens include particles, names and OCR. Do not interpret state=known as demonstrated reading recognition.'}}
    assert not db.new and not db.dirty and not db.deleted
assert hashlib.sha256(a.db.read_bytes()).hexdigest()==before
encoded=(json.dumps(out,ensure_ascii=False,indent=2,default=str)+'\n').encode('utf-8')
if a.out.suffix=='.gz':
    a.out.write_bytes(gzip.compress(encoded,mtime=0))
else:
    a.out.write_bytes(encoded)
print(json.dumps({'qac_suspects':suspects,'qac_pos_mismatch_count':len(mismatches),'momo_counts':dict(counts),'candidate_count':len(candidate_output)},ensure_ascii=False,indent=2))
