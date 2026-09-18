"""Read-only descriptive audit for the September 5 project reassessment.

Run from the repository root with backend/.venv/bin/python. No application
session is created. Historical token totals use current sentence mappings;
they are approximate volume, not immutable historical text reconstructions.
"""
import collections
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.sentence_validator import is_function_word_lemma

DB = pathlib.Path.home() / 'alif-backups/alif_20260905_092305.db'
LOGS = pathlib.Path.home() / 'alif-backups/logs'
OUT = pathlib.Path(__file__).resolve().parent
UTC = dt.timezone.utc
CUTOFF = dt.datetime(2026, 9, 3, 0, tzinfo=UTC)

def date(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

def percent(n, d):
    return round(100*n/d, 2) if d else None

def digest(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()

before = digest(DB)
c = sqlite3.connect(f'file:{DB}?mode=ro&immutable=1', uri=True)
c.row_factory = sqlite3.Row
c.execute('pragma query_only=on')
lemmas = {r['lemma_id']: dict(r) for r in c.execute('select * from lemmas')}

def canonical(i):
    seen = set()
    while i in lemmas and lemmas[i]['canonical_lemma_id'] and i not in seen:
        seen.add(i)
        i = lemmas[i]['canonical_lemma_id']
    return i

valid = {i for i,l in lemmas.items() if l['word_category'] not in ('proper_name','onomatopoeia')
         and not is_function_word_lemma(l['lemma_ar_bare'], l['function_word_override'])}
valid_canonical = {canonical(i) for i in valid} & valid
knowledge = [dict(r) for r in c.execute('select * from user_lemma_knowledge')]
canon_knowledge = [r for r in knowledge if r['lemma_id'] in valid_canonical and canonical(r['lemma_id']) == r['lemma_id']]
raw_reviews = [dict(r) for r in c.execute('select * from review_log order by reviewed_at,id')]
reviews = []
last = {}
latest_gap = {g:{} for g in (7,14,30)}
monthly = collections.defaultdict(lambda: {'judgments':0, 'clean':0, 'days':set(), 'lemmas':set()})
for r in raw_reviews:
    i = canonical(r['lemma_id'])
    if i not in valid_canonical or r['review_mode'] != 'reading' or r['sentence_id'] is None:
        continue
    t = date(r['reviewed_at'])
    # Longitudinal recognition comparisons stop before the new exposure-only
    # policy. Beyond that point ReviewLog alone is not a complete exposure clock.
    if t >= CUTOFF:
        continue
    r['canonical'] = i
    r['time'] = t
    r['clean'] = r['rating'] >= 3 and not r['was_confused']
    r['gap'] = (t-last[i]).total_seconds()/86400 if i in last else None
    last[i] = t
    for g in latest_gap:
        if r['gap'] is not None and r['gap'] >= g:
            latest_gap[g][i] = r['clean']
    reviews.append(r)
    m = monthly[t.strftime('%Y-%m')]
    m['judgments'] += 1
    m['clean'] += r['clean']
    m['days'].add(t.date().isoformat())
    m['lemmas'].add(i)

sentence_info = {r['id']:dict(r) for r in c.execute('select id,source,kind,arabic_text from sentences')}
word_counts = dict(c.execute('select sentence_id,count(*) from sentence_words group by sentence_id').fetchall())
events = []
event_counts = collections.Counter()
book_events = []
malformed = 0
log_manifest = []
seen_events = set()
for p in sorted(LOGS.glob('interactions_*.jsonl')):
    log_manifest.append({'name':p.name, 'bytes':p.stat().st_size, 'sha256':digest(p)})
    for line in p.open():
        try: e = json.loads(line)
        except (ValueError, TypeError):
            malformed += 1
            continue
        event_counts[e.get('event')] += 1
        if 'book' in str(e.get('event')) or 'reader' in str(e.get('event')):
            book_events.append(e)
        if e.get('event') != 'sentence_review' or e.get('review_mode','reading') != 'reading':
            continue
        key = (e.get('ts'),e.get('session_id'),e.get('sentence_id'))
        if key in seen_events:
            continue
        seen_events.add(key)
        events.append(e)

card_monthly = collections.defaultdict(list)
for e in events:
    card_monthly[e['ts'][:7]].append(e)

def cards_summary(es):
    # Parent events are cards; a passage's child sentence IDs are counted once.
    words = 0
    kinds = collections.Counter()
    sources = collections.Counter()
    times = []
    for e in es:
        ids = list(dict.fromkeys(e.get('sentence_ids') or [e.get('sentence_id')]))
        words += sum(word_counts.get(i,0) for i in ids)
        kinds[e.get('parent_card_type','unspecified')] += 1
        source_set = {sentence_info.get(i,{}).get('source','missing') for i in ids}
        sources['+'.join(sorted(source_set))] += 1
        ms = e.get('response_ms')
        if isinstance(ms,(int,float)) and 0 < ms < 1200000:
            times.append(ms/1000)
    days = len({e['ts'][:10] for e in es})
    return {'cards':len(es),'active_days':days,'kinds':dict(kinds), 'sources':dict(sources),
            'mapped_running_tokens_approx':words, 'timed_cards':len(times),
            'median_card_seconds':round(statistics.median(times),2) if times else None,
            'recorded_card_hours':round(sum(times)/3600,2)}

gap_bands = [(0,1,'<1d'),(1,3,'1-3d'),(3,7,'3-7d'),(7,14,'7-14d'),(14,30,'14-30d'),(30,99999,'30d+')]
windows = {}
for label,start,end in [('previous30',CUTOFF-dt.timedelta(days=60),CUTOFF-dt.timedelta(days=30)),
                        ('latest30',CUTOFF-dt.timedelta(days=30),CUTOFF)]:
    rs = [r for r in reviews if start<=r['time']<end]
    bands = []
    for lo,hi,band in gap_bands:
        rr = [r for r in rs if r['gap'] is not None and lo<=r['gap']<hi]
        bands.append({'band':band,'n':len(rr),'clean_pct':percent(sum(r['clean'] for r in rr),len(rr))})
    es = [e for e in events if start<=date(e['ts'])<end]
    windows[label] = {'start':start.isoformat(),'end':end.isoformat(),'judgments':len(rs),
                      'clean_pct':percent(sum(r['clean'] for r in rs),len(rs)),
                      'gaps':bands,'cards':cards_summary(es)}

evidence = {}
for sql,label in [
    ('select min(created_at),max(created_at),count(*),count(distinct client_review_id) from word_review_evidence','scope'),
    ('select protocol_version,count(*) from word_review_evidence group by 1','protocols'),
    ('select rating_source,count(*) from word_review_evidence group by 1','rating_sources'),
    ('select failure_causes_json,count(*) from word_review_evidence where failure_causes_json not in (\'[]\',\'null\') group by 1','causes')]:
    evidence[label] = [list(r) for r in c.execute(sql)]

stories = []
for r in c.execute('select id,title_en,source,status,metadata_json from stories'):
    meta = json.loads(r['metadata_json'] or '{}') or {}
    if meta.get('book_reader'):
        stories.append({'id':r['id'],'title':r['title_en'],'reader':meta['book_reader']})

data = {
    'provenance':{'db':str(DB),'sha256':before,'db_quick_check':c.execute('pragma quick_check').fetchone()[0],
                  'script_sha256':digest(pathlib.Path(__file__)), 'recognition_cutoff_exclusive':CUTOFF.isoformat(),
                  'log_files':len(log_manifest),'malformed_log_lines':malformed},
    'raw_review_scope':{'rows':len(raw_reviews),'first':raw_reviews[0]['reviewed_at'],'last':raw_reviews[-1]['reviewed_at']},
    'canonical_state_counts':dict(collections.Counter(r['knowledge_state'] for r in canon_knowledge)),
    'recognition_scope':{'rows':len(reviews),'canonical_lemmas':len(last),'first':reviews[0]['reviewed_at'],'last':reviews[-1]['reviewed_at']},
    'latest_demonstrated_gap':{str(g):{'tested':len(v),'latest_clean':sum(v.values())} for g,v in latest_gap.items()},
    'monthly':{k:{'judgments':v['judgments'],'clean_pct':percent(v['clean'],v['judgments']),
                  'active_days':len(v['days']),'lemmas':len(v['lemmas'])} for k,v in monthly.items()},
    'card_monthly':{k:cards_summary(v) for k,v in card_monthly.items()},
    'card_lifetime':cards_summary(events), 'windows':windows,
    'word_evidence':evidence, 'event_counts':dict(event_counts),
    'book_events':[e for e in book_events if e.get('event') not in ('book_word_lookup',)],
    'book_reader_metadata':stories,
    'grammar_exposure_rows':c.execute('select count(*) from user_grammar_exposure').fetchone()[0],
    'grammar_feature_count':c.execute('select count(*) from grammar_features').fetchone()[0],
    'log_manifest':log_manifest,
}
c.close()
assert digest(DB) == before, 'Snapshot changed during analysis'
(OUT/'evidence.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in data.items() if k not in ('log_manifest','event_counts','book_events','book_reader_metadata')},ensure_ascii=False,indent=2))
print('BOOK EVENTS',collections.Counter(e['event'] for e in book_events))
print('BOOK PROGRESS', json.dumps(stories,ensure_ascii=False)[:5000])
