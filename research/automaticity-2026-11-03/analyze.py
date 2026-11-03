"""Reproduce the November automaticity audit against a read-only production snapshot."""
import argparse, collections, gzip, hashlib, json, sqlite3, statistics, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from fsrs import Card, Scheduler

p=argparse.ArgumentParser(); p.add_argument('--db',required=True); p.add_argument('--logs',required=True); p.add_argument('--output',required=True); a=p.parse_args()
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/scripts'))
from analyze_learning_system import load_lemmas, invalid_lemma_reason

def dt(s):
    return datetime.fromisoformat(s.replace('Z','+00:00')).replace(tzinfo=timezone.utc)
def js(x):
    if isinstance(x,str): return json.loads(x or '{}') or {}
    return x or {}
def pct(n,d): return round(100*n/d,1) if d else None
def summary(xs):
    return {'n':len(xs),'median':round(statistics.median(xs),2) if xs else None}

before=hashlib.sha256(Path(a.db).read_bytes()).hexdigest(); db=sqlite3.connect(f'file:{Path(a.db).resolve()}?mode=ro&immutable=1',uri=True); db.row_factory=sqlite3.Row
lemmas,roots=load_lemmas(db)
labels={r['lemma_id']:dict(r) for r in db.execute('select lemma_id,lemma_ar,lemma_ar_bare,gloss_en,function_word_override,word_category from lemmas')}
def valid(lid):
    if lid not in lemmas or roots.get(lid,lid)!=lid:return False
    l=labels[lid]
    if l['word_category'] in ('proper_name','onomatopoeia'):return False
    if l['function_word_override'] is not None:return not bool(l['function_word_override'])
    return not invalid_lemma_reason(lemmas[lid])
logs=[]
for f in sorted(Path(a.logs).glob('interactions_*.jsonl*')):
    with (gzip.open if f.suffix=='.gz' else open)(f,'rt') as h:
        for line in h:
            try: logs.append(json.loads(line))
            except json.JSONDecodeError: pass
cutoff=max(dt(r[0]) for r in db.execute('select max(reviewed_at) from sentence_review_log'))+timedelta(seconds=1)
# Deduplicate online/offline logging; match persisted rows by stable IDs where available,
# otherwise sentence/session. Old logs lack the base client_review_id.
event_by_key={}
for e in logs:
    if e.get('event')=='sentence_review': event_by_key[(e.get('session_id'),e.get('sentence_id'))]=e
sentences=[dict(r) for r in db.execute("select * from sentence_review_log where review_mode='reading'")]
token_flags={r['client_review_id']:dict(r) for r in db.execute('select client_review_id,count(*) n,min(rating) min_rating,max(front_toggle_count) toggles,max(answer_revealed) revealed from word_review_evidence group by client_review_id')}
periods=[]
for name,start,end in [('Sep',datetime(2026,9,1,tzinfo=timezone.utc),datetime(2026,10,1,tzinfo=timezone.utc)),('Oct',datetime(2026,10,1,tzinfo=timezone.utc),datetime(2026,11,1,tzinfo=timezone.utc)),('last14',cutoff-timedelta(days=14),cutoff),('last7',cutoff-timedelta(days=7),cutoff),('today',datetime(2026,11,3,tzinfo=ZoneInfo('Europe/Oslo')).astimezone(timezone.utc),cutoff)]:
    rows=[s for s in sentences if start<=dt(s['reviewed_at'])<end]
    active=len({dt(s['reviewed_at']).astimezone(ZoneInfo('Europe/Oslo')).date() for s in rows}); days=max(1,(end-start).total_seconds()/86400)
    clean=[s for s in rows if s['comprehension']=='understood']; assisted_unknown=0; proxies=[]
    for s in clean:
        e=event_by_key.get((s['session_id'],s['sentence_id'])); flags=token_flags.get(s['client_review_id'])
        if not e or e.get('word_evidence_protocol_version') != 3 or not flags: assisted_unknown+=1; continue
        if not e.get('lookup_count',0) and not e.get('audio_play_count',0) and not e.get('missed_lemma_ids') and not e.get('confused_lemma_ids') and flags['min_rating']>=3 and flags['toggles']==0 and e.get('parent_card_type') in (None,'sentence'):
            proxies.append(s)
    times=[s['response_ms']/1000 for s in proxies if s['response_ms'] and 500<=s['response_ms']<300000]
    periods.append({'period':name,'cards':len(rows),'active_days':active,'cards_per_calendar_day':round(len(rows)/days,1),'cards_per_active_day':round(len(rows)/active,1) if active else None,'understood':len(clean),'understood_pct':pct(len(clean),len(rows)),'unassisted_proxy':len(proxies),'unassisted_proxy_pct_all':pct(len(proxies),len(rows)),'clean_missing_help_evidence':assisted_unknown,'legacy_proxy_seconds':summary(times),'card_timing_sum_minutes_capped_5m':round(sum(min(s['response_ms'] or 0,300000) for s in rows)/60000,1)})
# Due primary reviews: sample once per target sentence, excluding shadow/proper/function entries.
# pre_card last_review is a scheduling interval, NOT guaranteed time since last exposure.
scheduler=Scheduler(); curves=collections.defaultdict(list); calibration=collections.defaultdict(list); late=[]; primary=collections.defaultdict(list)
for r in db.execute("select * from review_log where reviewed_at >= '2026-09-01' and review_mode='reading' and sentence_id is not null and credit_type='primary' order by reviewed_at,id"):
    r=dict(r); lid=r['lemma_id']
    if not valid(lid): continue
    t=dt(r['reviewed_at']); primary[t.strftime('%Y-%m')].append(r['rating']>=3 and not r['was_confused'])
    m=js(r['fsrs_log_json']); pre=m.get('pre_card')
    if r['is_acquisition'] or not pre: continue
    try:
        c=Card.from_dict(pre)
        if not c.last_review or not c.stability or c.due>t:continue
        gap=(t-c.last_review).total_seconds()/86400; band='<7d' if gap<7 else '7–14d' if gap<14 else '14–30d' if gap<30 else '30–60d' if gap<60 else '60d+'
        success=r['rating']>=3 and not r['was_confused']; pred=scheduler.get_card_retrievability(c,current_datetime=t)
        curves[(t.strftime('%Y-%m'),band)].append(success); calibration[t.strftime('%Y-%m')].append((pred,success)); late.append((t-c.due).total_seconds()/86400)
    except (ValueError,TypeError,KeyError):pass
states=collections.Counter(); dispositions=collections.Counter(); due=0; future=collections.Counter(); models=[]; stability=[]
for row in db.execute('select * from user_lemma_knowledge'):
    r=dict(row)
    if not valid(r['lemma_id']):continue
    dispositions[r['attention_disposition']]+=1
    if r['attention_disposition']!='maintain':continue
    states[r['knowledge_state']]+=1
    if r['knowledge_state'] not in ('known','learning','lapsed') or not r['fsrs_card_json']:continue
    try:
        c=Card.from_dict(js(r['fsrs_card_json']))
        if not c.stability or not c.last_review:continue
        stability.append(c.stability); due+=int(c.due<=cutoff)
        for day in [0,7,14,30]:
            future[day]+=int(c.due<=cutoff+timedelta(days=day))
        models.append([scheduler.get_card_retrievability(c,current_datetime=cutoff+timedelta(days=d)) for d in [0,7,14,30]])
    except (KeyError,TypeError,ValueError):pass
caps=[dict(r) for r in db.execute('select * from confusion_captures order by captured_at')]; edges=collections.Counter(); unresolved=[]
for c in caps:
    lid=roots.get(c['failed_lemma_id'],c['failed_lemma_id']); target=c['confused_with_lemma_id'] or c['resolved_lemma_id']
    if target:edges[tuple(sorted([lid,roots.get(target,target)]))]+=1
    else:unresolved.append(c)
def label(i):return {'id':i,'arabic':labels.get(i,{}).get('lemma_ar'),'gloss':labels.get(i,{}).get('gloss_en')}
clusters=[]; graph=collections.defaultdict(set)
for x,y in edges:graph[x].add(y);graph[y].add(x)
seen=set()
for n in graph:
    if n in seen:continue
    todo=[n];component=set()
    while todo:
        v=todo.pop()
        if v in component:continue
        component.add(v);todo.extend(graph[v]-component)
    seen.update(component); clusters.append({'words':[label(i) for i in sorted(component)],'captures':sum(v for (x,y),v in edges.items() if x in component),'repeated_edge':any(v>1 for (x,y),v in edges.items() if x in component)})
# Explicitly requested meanings: candidates only, never infer that a dictionary neighbor was confused.
seed_candidates=[label(i) for i,l in lemmas.items() if (l.get('lemma_ar_bare') in ['سباحة','صباح','وجه','وجع','تجاه','اتجاه'])]
result={'snapshot_sha256':before,'cutoff':cutoff.isoformat(),'production_source_head':'6f3c02e6','periods':periods,'primary_clean_by_month':{k:{'n':len(v),'pct':pct(sum(v),len(v))} for k,v in primary.items()},'due_primary_gap_curves':[{'month':k[0],'gap':k[1],'n':len(v),'clean_pct':pct(sum(v),len(v))} for k,v in curves.items()],'due_primary_calibration':{k:{'n':len(v),'predicted_pct':round(100*statistics.mean(x[0] for x in v),1),'strict_clean_pct':pct(sum(x[1] for x in v),len(v))} for k,v in calibration.items()},'lateness_days':summary(late),'attention_dispositions':dict(dispositions),'maintained_states':dict(states),'modeled_cards':len(models),'median_stability_days':statistics.median(stability),'due_now':due,'new_introductions_by_month':dict(db.execute("select substr(introduced_at,1,7),count(*) from user_lemma_knowledge where introduced_at >= '2026-09-01' and (acquisition_episode_kind is null or acquisition_episode_kind != 'leech_reintro') group by 1").fetchall()),'due_cumulative_if_no_reviews':dict(future),'mean_predicted_recall_if_no_reviews':{d:round(100*statistics.mean(x[j] for x in models),1) for j,d in enumerate([0,7,14,30])},'confusions':{'n':len(caps),'latest':caps[-1]['captured_at'],'methods':dict(collections.Counter(c['capture_method'] for c in caps)),'unresolved_free_text':len(unresolved),'clusters':sorted(clusters,key=lambda x:-x['captures']),'unresolved':[{'failed':label(c['failed_lemma_id']),'text':c['confused_with_text'],'date':c['captured_at']} for c in unresolved],'memo_seed_candidates':seed_candidates}}
assert before==hashlib.sha256(Path(a.db).read_bytes()).hexdigest(); Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)); print(json.dumps(result['periods'],indent=2)); print(json.dumps(result['confusions']['clusters'][:6],ensure_ascii=False,indent=2)); print(json.dumps(seed_candidates,ensure_ascii=False))
