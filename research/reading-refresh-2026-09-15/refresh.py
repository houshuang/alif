"""Read-only September 15 checkpoint supplement; run with Python 3.11+.

Uses committed classification and debt helpers. Timing is recorded submission
latency, not active reading. No live database connections or app mutations.
"""
import collections
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/scripts'))
from analyze_learning_system import open_read_only, load_lemmas, analyze_current_state, parse_datetime
from app.services.sentence_validator import is_function_word_lemma

DB = pathlib.Path('/Users/stian/alif-backups/alif_20260915_092255.db')
LOGS = pathlib.Path('/Users/stian/.agents/alif-reading-refresh-20260915/logs')
OUT = pathlib.Path(__file__).parent
START = parse_datetime('2026-09-06T00:00:00Z')
POLICY = parse_datetime('2026-09-03T10:00:00Z')
CUTOFF = parse_datetime('2026-09-15T07:22:55Z')
OLD = POLICY - dt.timedelta(days=90)

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def summary(rows):
    n = len(rows)
    good = sum(r['clean'] for r in rows)
    return {'n': n, 'clean': good, 'clean_pct': round(100*good/n, 1) if n else None,
            'distinct_lemmas': len({r['lemma_id'] for r in rows})}

before = digest(DB)
c = open_read_only(DB)
lemmas = {r['lemma_id']: dict(r) for r in c.execute('select * from lemmas')}
_, canonical = load_lemmas(c)
valid = {i for i,l in lemmas.items() if canonical[i] == i
         and l['word_category'] not in ('proper_name','onomatopoeia')
         and not is_function_word_lemma(l['lemma_ar_bare'],l['function_word_override'])}
ulk = {r['lemma_id']:dict(r) for r in c.execute('select * from user_lemma_knowledge')}
states = collections.Counter(r['knowledge_state'] for i,r in ulk.items() if i in valid)
old_ids = {i for i,r in ulk.items() if i in valid and
           (t := parse_datetime(r['introduced_at'] or r['acquisition_started_at'] or
                                 r['entered_acquiring_at'] or r['graduated_at'])) and t <= OLD}

# Treat one client review / canonical lemma as one observation, even if repeated
# tokens occur. Supplement its exposure clock with quiz/legacy ReviewLog rows.
observations = []
linked = set()
evidence = [dict(r) for r in c.execute('select * from word_review_evidence order by created_at,id')]
grouped = collections.defaultdict(list)
for r in evidence:
    grouped[(r['client_review_id'],r['canonical_lemma_id'])].append(r)
    if r['review_log_id'] is not None:
        linked.add(r['review_log_id'])
for (client,i),rs in grouped.items():
    if i not in valid or not any(r['is_schedulable_content'] for r in rs): continue
    observations.append({'t':min(parse_datetime(r['created_at']) for r in rs),
                         'lemma_id':i, 'clean':all(r['rating']>=3 for r in rs),
                         'scheduled':any(r['review_log_id'] is not None for r in rs),
                         'reading':rs[0]['review_mode']=='reading', 'source':'token'})
for r in c.execute('select * from review_log order by reviewed_at,id'):
    i = canonical.get(r['lemma_id'],r['lemma_id'])
    if r['id'] in linked or i not in valid: continue
    observations.append({'t':parse_datetime(r['reviewed_at']), 'lemma_id':i,
                         'clean':r['rating']>=3 and not r['was_confused'],
                         'scheduled':True, 'reading':r['review_mode']=='reading' and r['sentence_id'] is not None,
                         'source':r['review_mode']})
last_any,last_scheduled = {},{}
for r in sorted(observations,key=lambda r:r['t']):
    i,t = r['lemma_id'],r['t']
    r['all_recorded_gap'] = (t-last_any[i]).total_seconds()/86400 if i in last_any else None
    r['scheduled_gap'] = (t-last_scheduled[i]).total_seconds()/86400 if i in last_scheduled else None
    last_any[i]=t
    if r['scheduled']:last_scheduled[i]=t

windows={}
for label,lo,hi in [('pre_policy',POLICY-dt.timedelta(days=12),POLICY),
                     ('policy',POLICY,CUTOFF),('since_assessment',START,CUTOFF),
                     ('early',POLICY,parse_datetime('2026-09-10T00:00:00Z')),
                     ('recent',parse_datetime('2026-09-10T00:00:00Z'),CUTOFF)]:
    rs=[r for r in observations if lo<=r['t']<hi and r['scheduled'] and r['reading']]
    windows[label]={'scheduled_reading':summary(rs)}
    for clock in ('scheduled_gap','all_recorded_gap'):
        windows[label][clock]={str(g):summary([r for r in rs if r['lemma_id'] in old_ids and
                                              r[clock] is not None and r[clock]>=g]) for g in (7,14,30)}

# Matched words, last eligible old-word >=7d observation in each window.
paired=[]
periods=[]
for lo,hi in [(POLICY-dt.timedelta(days=12),POLICY),(POLICY,CUTOFF)]:
    by_id={}
    for r in sorted(observations,key=lambda r:r['t']):
        if lo<=r['t']<hi and r['scheduled'] and r['reading'] and r['lemma_id'] in old_ids and (r['all_recorded_gap'] or 0)>=7:
            by_id[r['lemma_id']]=r
    periods.append(by_id)
for i in sorted(periods[0].keys() & periods[1].keys()):
    paired.append({'lemma_id':i,'before':periods[0][i]['clean'],'after':periods[1][i]['clean']})

sr=[dict(r) for r in c.execute('select * from sentence_review_log order by reviewed_at,id')]
daily=[]
for day in sorted({r['reviewed_at'][:10] for r in sr if START<=parse_datetime(r['reviewed_at'])<CUTOFF}):
    rs=[r for r in sr if r['reviewed_at'][:10]==day and r['review_mode']=='reading']
    durations=[r['response_ms']/1000 for r in rs if isinstance(r['response_ms'],(int,float)) and r['response_ms']>=0]
    daily.append({'utc_day':day,'completed_cards':len(rs),'fully_understood':sum(r['comprehension']=='understood' for r in rs),
                  'median_recorded_seconds':statistics.median(durations) if durations else None})

recent_e=[r for r in evidence if START<=parse_datetime(r['created_at'])<CUTOFF]
cause_counts=collections.Counter()
for r in recent_e:
    causes=json.loads(r['failure_causes_json'] or 'null') or []
    cause_counts.update(causes)
failures=collections.defaultdict(list)
for r in recent_e:
    if r['canonical_lemma_id'] in valid and r['rating']<3:
        failures[r['canonical_lemma_id']].append(r)
blockers=[]
for i,rs in sorted(failures.items(),key=lambda kv:len({r['client_review_id'] for r in kv[1]}),reverse=True)[:15]:
    blockers.append({'lemma_id':i,'arabic':lemmas[i]['lemma_ar'],'gloss':lemmas[i]['gloss_en'],
                     'distinct_review_misses':len({r['client_review_id'] for r in rs}),
                     'days':sorted({r['created_at'][:10] for r in rs}),
                     'surfaces':sorted({r['surface_form'] for r in rs})})

events=[];manifest=[];bad=0
for p in sorted(LOGS.glob('interactions_*.jsonl*')):
    op=gzip.open if p.suffix=='.gz' else open
    relevant=False
    for line in op(p,'rt'):
        try:e=json.loads(line)
        except ValueError:bad+=1;continue
        t=parse_datetime(e.get('ts'))
        if t and POLICY<=t<CUTOFF:events.append(e);relevant=True
    if relevant:manifest.append({'file':p.name,'sha256':digest(p),'bytes':p.stat().st_size})

stories=[]
for r in c.execute('select id,title_en,created_at,completed_at,metadata_json from stories'):
    m=json.loads(r['metadata_json'] or 'null') or {}
    if m.get('book_reader'):stories.append({'id':r['id'],'title':r['title_en'],'progress':m['book_reader']})

debt=[]
for p in sorted(DB.parent.glob('alif_202609*.db')):
    if p.name>DB.name:continue
    stamp=dt.datetime.strptime(p.stem[5:],'%Y%m%d_%H%M%S').replace(tzinfo=__import__('zoneinfo').ZoneInfo('Europe/Oslo')).astimezone(dt.timezone.utc)
    conn=open_read_only(p);ls,cs=load_lemmas(conn)
    state,_,warn=analyze_current_state(conn,stamp,ls,cs)
    debt.append({'snapshot':p.name,'at':stamp.isoformat(),'values':state['recovery']['values'],'acquiring':state['acquisition_total']});conn.close()

result={'provenance':{'snapshot':str(DB),'sha256':before,'cutoff':CUTOFF.isoformat(),'source_commit':'2d859666042aa02054f1ff6434ac7cf68ba949db','production_commit':'5f212bcaaf51a3096eb768e213f15a27712728ef','script_sha256':digest(pathlib.Path(__file__)),'logs':manifest},
        'canonical_content_state_counts':dict(states),'old_cohort_words':len(old_ids),'windows':windows,
        'matched_old_7day':{'n':len(paired),'before_clean':sum(r['before'] for r in paired),'after_clean':sum(r['after'] for r in paired),'pairs':paired},
        'daily_completed':daily,'recent_token_evidence':{'rows':len(recent_e),'rating_sources':dict(collections.Counter(r['rating_source'] for r in recent_e)),'failure_cause_tags':dict(cause_counts),'tagged_rows':sum(bool(json.loads(r['failure_causes_json'] or 'null')) for r in recent_e)},
        'recent_repeated_blockers':blockers,'snapshot_debt':debt,'book_reader_metadata':stories,
        'reading_events_since_policy':dict(collections.Counter(e['event'] for e in events if any(x in e.get('event','') for x in ['book','reader','pilot']))),
        'raw_sentence_review_event_count':sum(e.get('event')=='sentence_review' for e in events),
        'database_unchanged':before==digest(DB)}
assert result['database_unchanged']
(OUT/'supplement.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('provenance','book_reader_metadata','snapshot_debt')},ensure_ascii=False,indent=2))
