"""Read-only vocabulary/attention audit of a pinned production SQLite backup.

Run with backend/.venv/bin/python; pass --db, --data-dir and --output-dir.
No app engine connections, API calls, provider calls, or production mutations.
Current frequency classifications are retrospective proxies, not historical ranks.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['ALIF_SKIP_MIGRATIONS'] = '1'
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
from app.services.sentence_validator import is_function_word_lemma
from app.services import lemma_quality
from app.services.frequency_lanes import is_main_lane_word


def dt(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=timezone.utc)


def pct(n, d):
    return round(100*n/d, 2) if d else None


def band(r):
    if r is None:
        return 'unknown'
    for edge, label in [(1000, '1-1000'), (2000, '1001-2000'), (5000, '2001-5000'), (20000, '5001-20000')]:
        if r <= edge:
            return label
    return '>20000'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', type=Path, required=True)
    ap.add_argument('--data-dir', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    ap.add_argument('--cutoff', default='2026-09-19T13:17:00Z')
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(args.db.read_bytes()).hexdigest()
    con = sqlite3.connect(f'file:{args.db.resolve()}?mode=ro&immutable=1', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('pragma query_only=on')
    rows = lambda q: [dict(r) for r in con.execute(q)]
    lem = {r['lemma_id']: r for r in rows('select * from lemmas')}
    ulk = {r['lemma_id']: r for r in rows('select * from user_lemma_knowledge')}
    def canon(i):
        seen = set()
        while i in lem and lem[i]['canonical_lemma_id'] and i not in seen:
            seen.add(i)
            i = lem[i]['canonical_lemma_id']
        return i
    valid = {i for i, l in lem.items() if canon(i) == i
             and l['word_category'] not in ('proper_name', 'onomatopoeia')
             and not is_function_word_lemma(l['lemma_ar_bare'], l['function_word_override'])}
    active = {i for i in valid if ulk.get(i, {}).get('knowledge_state') in ('known', 'learning', 'lapsed', 'acquiring')}
    fces = rows('select * from frequency_core_entries where excluded_reason is null')
    fce = {}
    for r in fces:
        i = canon(r['lemma_id'])
        if i is not None and (i not in fce or r['core_rank'] < fce[i]['core_rank']):
            fce[i] = r
    lemma_quality._CAMEL_CACHE = args.data_dir / 'MSA_freq_lists.tsv'
    rank_map = lemma_quality._load_rank_map()
    ranks, stored_ranks, repaired_ranks, article_ranks = {}, {}, {}, {}
    for i, l in lem.items():
        probe = SimpleNamespace(lemma_ar_bare=l['lemma_ar_bare'], frequency_rank=l['frequency_rank'])
        if probe.frequency_rank is None:
            lemma_quality.assign_frequency_rank(probe)
        repaired_ranks[i] = probe.frequency_rank
        core = fce.get(i, {}).get('core_rank')
        ranks[i] = min([r for r in (core, probe.frequency_rank) if r and r>0], default=None)
        stored_ranks[i] = min([r for r in (core, l['frequency_rank']) if r and r>0], default=None)
        b = lemma_quality._normalize(l['lemma_ar_bare'])
        article_ranks[i] = rank_map.get('ال'+b)
    cutoff = dt(args.cutoff)
    reviews = rows('select * from review_log order by reviewed_at,id')
    for r in reviews:
        r['canonical'] = canon(r['lemma_id'])
        r['t'] = dt(r['reviewed_at'])
    sentences = {r['id']: r for r in rows('select id,source,kind,target_lemma_id,is_active from sentences')}
    srl = rows('select * from sentence_review_log order by reviewed_at,id')
    sws = rows('select sentence_id,lemma_id,is_target_word from sentence_words')
    sentence_lemmas = defaultdict(set)
    corpus_counts, momo_counts = Counter(), Counter()
    for r in sws:
        i = canon(r['lemma_id']); s = sentences.get(r['sentence_id'], {})
        if i not in valid:
            continue
        sentence_lemmas[r['sentence_id']].add(i)
        if s.get('source') == 'corpus':
            corpus_counts[i] += 1
        if s.get('kind') == 'momo_book':
            momo_counts[i] += 1
    bm = json.loads((args.data_dir/'benchmarks/book_momo_tokenmap.json').read_text())
    book_counts = Counter()
    for raw, n in bm['mapped'].items():
        book_counts[canon(int(raw))] += n
    per_word = defaultdict(Counter)
    windows = {}
    for name, start in [('june','2026-06-01'),('july','2026-07-01'),('august','2026-08-01'),('september','2026-09-01'),('maintenance','2026-09-03T10:00:00Z')]:
        lo = dt(start)
        hi = dt({'june':'2026-07-01','july':'2026-08-01','august':'2026-09-01'}.get(name, args.cutoff))
        rr = [r for r in reviews if lo <= r['t'] < hi and r['canonical'] in valid and r['review_mode']=='reading' and r['sentence_id'] is not None]
        cards = [r for r in srl if lo <= dt(r['reviewed_at']) < hi and r['review_mode']=='reading']
        groups = defaultdict(Counter)
        for r in rr:
            i=r['canonical']; g=groups[band(ranks[i])]
            g['judgments']+=1;g['clean']+=int(r['rating']>=3 and not r['was_confused'])
            g[r['credit_type'] or 'unknown_credit']+=1
            g['acquisition']+=int(bool(r['is_acquisition']))
            if name=='maintenance':
                p=per_word[i];p['judgments']+=1;p['failures']+=int(r['rating']<3 or r['was_confused']);p[r['credit_type'] or 'unknown_credit']+=1
        for r in cards:
            s=sentences.get(r['sentence_id'],{});i=canon(s.get('target_lemma_id'))
            if i in valid and name=='maintenance':
                per_word[i]['target_cards']+=1
            ids=sentence_lemmas[r['sentence_id']]
            rare={i for i in ids if ranks[i] is not None and ranks[i]>5000}
            unknown={i for i in ids if ranks[i] is None}
            label='has_rank_gt5000' if rare else 'has_unknown_rank' if unknown else 'all_ranked_top5000'
            groups[label]['cards']+=1
            if r['response_ms'] and 0<r['response_ms']<=300000:
                groups[label]['capped_response_ms']+=r['response_ms']
        daily=Counter(r['reviewed_at'][:10] for r in cards)
        times=[r['response_ms']/1000 for r in cards if r['response_ms'] and r['response_ms']>0]
        windows[name]={'judgments':len(rr),'primary':sum(r['credit_type']=='primary' for r in rr),
                       'cards':len(cards),'active_days':len(daily),'median_cards_active_day':median(daily.values()) if daily else None,
                       'median_response_seconds':median(times) if times else None,'groups':dict(groups),
                       'card_sources':dict(Counter(sentences.get(r['sentence_id'],{}).get('source') for r in cards))}
    # Exposure-only rows must not inflate scheduled review counts. Collapse repeated tokens.
    evidence=rows("select client_review_id,canonical_lemma_id,review_log_id,rating,created_at from word_review_evidence where is_schedulable_content=1 and review_mode='reading'")
    exposures=set()
    for r in evidence:
        if dt('2026-09-03T10:00:00Z')<=dt(r['created_at'])<cutoff and r['review_log_id'] is None:
            exposures.add((r['client_review_id'],canon(r['canonical_lemma_id'])))
    # Current debt uses canonical content only; calculate both stored and repaired inputs.
    debt=defaultdict(Counter)
    for i in active:
        u=ulk[i];l=lem[i]
        if u['knowledge_state']=='acquiring':
            due=dt(u['acquisition_next_due'])
        else:
            card=u['fsrs_card_json']
            if isinstance(card,str):card=json.loads(card)
            due=dt((card or {}).get('due'))
        if due and due<=cutoff:
            for mode,fr in [('stored',l['frequency_rank']),('repaired',repaired_ranks[i])]:
                main=is_main_lane_word(SimpleNamespace(**u),SimpleNamespace(**{**l,'frequency_rank':fr}),fce.get(i,{}).get('core_rank'))
                debt[mode]['main' if main else 'slow']+=1
                debt[mode]['fsrs' if u['knowledge_state']!='acquiring' else 'acquiring']+=1
    table=[]
    for i in sorted(valid):
        l=lem[i];u=ulk.get(i,{});f=fce.get(i,{})
        table.append({'lemma_id':i,'arabic':l['lemma_ar'],'gloss':l['gloss_en'],'state':u.get('knowledge_state'),
                      'source':u.get('source'),'lexical_source':l['source'],'active':i in active,
                      'core_rank':f.get('core_rank'),'stored_camel_rank':l['frequency_rank'],'repaired_camel_rank':repaired_ranks[i],
                      'effective_rank':ranks[i],'article_surface_rank':article_ranks[i],
                      'hindawi_rank':f.get('hindawi_rank'),'news_rank':f.get('news_rank'),'quran_rank':f.get('islamic_rank'),
                      'corpus_tokens':corpus_counts[i],'momo_excerpt_tokens':momo_counts[i],'momo_frozen_map_tokens':book_counts[i],
                      'leech_count':u.get('leech_count'),'lifetime_seen':u.get('times_seen'),
                      **{k:per_word[i][k] for k in ('judgments','failures','primary','collateral','target_cards')}})
    with (args.output_dir/'word-audit.csv').open('w') as out:
        w=csv.DictWriter(out,fieldnames=table[0],lineterminator='\n');w.writeheader();w.writerows(table)
    # Source/cohort costs and current state, not causal learning gains.
    source_stats={}
    for source in sorted({ulk[i]['source'] or 'none' for i in active}):
        ids={i for i in active if (ulk[i]['source'] or 'none')==source}
        allids={i for i in valid if (ulk.get(i,{}).get('source') or 'none')==source}
        source_stats[source]={'active':len(ids),'states':dict(Counter(ulk.get(i,{}).get('knowledge_state') for i in allids)),
                              'rank_gt5000':sum(ranks[i] is not None and ranks[i]>5000 for i in ids),
                              'unknown_rank':sum(ranks[i] is None for i in ids),
                              'judgments':sum(per_word[i]['judgments'] for i in allids),'failures':sum(per_word[i]['failures'] for i in allids),
                              'primary':sum(per_word[i]['primary'] for i in allids),
                              'target_cards':sum(per_word[i]['target_cards'] for i in allids)}
    # Frozen full-book benchmark: do not remap lossy bare OOV strings as running tokens.
    represented=Counter()
    for i,n in book_counts.items():
        represented[ulk.get(i,{}).get('knowledge_state','no_state')]+=n
    core_bands={}
    for n in (500,1000,2000,3000,5000):
        rr=[r for r in fces if r['core_rank']<=n]
        core_bands[str(n)]={'rows':len(rr),'mapped_rows':sum(r['lemma_id'] is not None for r in rr),
            'distinct_canonical_lemmas':len({canon(r['lemma_id']) for r in rr if r['lemma_id'] is not None}),
            'states':dict(Counter(ulk.get(canon(r['lemma_id']),{}).get('knowledge_state','no_state') for r in rr)),
            'sources':{s:sum(r[s+'_rank'] is not None for r in rr) for s in ('camel','hindawi','news','islamic','kelly','buckwalter','artenten')}}
    repaired_ids={i for i in lem if lem[i]['frequency_rank'] is None and repaired_ranks[i] is not None}
    result={'snapshot':{'sha256':digest,'cutoff':args.cutoff,'latest_review':reviews[-1]['reviewed_at'],'review_rows':len(reviews)},
            'valid_canonical_content':len(valid),'active_content':len(active),
            'active_stored_bands':dict(Counter(band(stored_ranks[i]) for i in active)),
            'active_repaired_bands':dict(Counter(band(ranks[i]) for i in active)),
            'rank_repair':{'all':len(repaired_ids),'active':len(repaired_ids & active)},
            'due':dict(debt),'windows':windows,'sources':source_stats,'core_bands':core_bands,
            'exposure_only_observations':len(exposures),
            'momo_frozen_benchmark':{'total':bm['total'],'function':bm['function'],'mapped_tokens':sum(book_counts.values()),
                 'unmapped_tokens':sum(bm['unmapped_freq'].values()),'mapped_current_states':dict(represented),
                 'warning':'July lossy map is stale; no absence proves a word absent from book; no fresh coverage claim'},
            'top_failure_words':sorted(table,key=lambda x:(-x['failures'],-x['judgments']))[:40],
            'top_gt5000_words':sorted([r for r in table if r['effective_rank'] is not None and r['effective_rank']>5000],key=lambda x:-x['judgments'])[:40],
            'top_core_surface_disagreements':sorted([r for r in table if r['core_rank'] and r['core_rank']<=2000 and (r['repaired_camel_rank'] or 10**9)>20000],key=lambda x:-x['judgments'])[:50]}
    result['database_unchanged']=hashlib.sha256(args.db.read_bytes()).hexdigest()==digest
    assert result['database_unchanged']
    (args.output_dir/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps({k:result[k] for k in ('snapshot','active_content','active_repaired_bands','rank_repair','due')},indent=2))


if __name__=='__main__':
    main()
