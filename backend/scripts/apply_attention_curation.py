#!/usr/bin/env python3
"""Apply reviewed exact-ID attention/core exclusions; dry-run by default.

Never rewrites memory or history. Validate the entire manifest before writing.
Requires an online production backup before --apply. Retain the JSON result as
preimage evidence; no corpus activation or frequency-core rebuild is performed.
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models import Lemma, FrequencyCoreEntry, UserLemmaKnowledge
from app.services.attention_policy import set_disposition, POLICY_VERSION
from app.services.activity_log import log_activity


def apply_curation(db, manifest: dict, apply: bool = False) -> dict:
    if manifest.get('policy_version') != POLICY_VERSION:
        raise ValueError('Unknown policy version')
    changes = []
    for item in manifest['entries']:
        expected = item['expected']
        lemma = db.get(Lemma, expected['lemma_id'])
        if lemma is None or lemma.canonical_lemma_id is not None or any(
            getattr(lemma, field) != value for field, value in expected.items()
        ):
            raise ValueError(f"Stale identity preimage for {expected['lemma_id']}")
        if 'disposition' in item:
            if item['disposition'] != 'parked':
                raise ValueError('Initial curation supports QA parking only')
            k = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
            if k and k.attention_disposition not in ('maintain', 'parked'):
                raise ValueError(f'New learner preference for {lemma.lemma_id}')
            changes.append({'lemma_id':lemma.lemma_id, 'kind':'attention',
                            'before':k.attention_disposition if k else None,
                            'before_reason':k.attention_reason if k else None,
                            'after':item['disposition'], 'reason':item['reason']})
        else:
            entry = db.query(FrequencyCoreEntry).filter_by(core_rank=item['exclude_core_rank']).one()
            reason = f"{POLICY_VERSION}:qac_identity_audit"
            if entry.lemma_id != lemma.lemma_id or entry.excluded_reason not in (None, reason):
                raise ValueError(f'Stale core preimage for {lemma.lemma_id}')
            changes.append({'lemma_id':lemma.lemma_id, 'kind':'core', 'core_rank':entry.core_rank,
                            'before':entry.excluded_reason, 'after':reason, 'reason':item['reason']})
    if apply:
        for change in changes:
            if change['before'] == change['after']:
                continue
            if change['kind'] == 'attention':
                set_disposition(db, change['lemma_id'], change['after'], change['reason'])
            else:
                entry = db.query(FrequencyCoreEntry).filter_by(core_rank=change['core_rank']).one()
                entry.excluded_reason = change['after']
        log_activity(db, 'attention_curation', 'Applied reviewed identity QA exclusions', {'policy_version':POLICY_VERSION,'changes':changes})
        db.commit()
    return {'policy_version':POLICY_VERSION,'applied':apply,'changes':changes}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    from app.database import SessionLocal
    with SessionLocal() as db:
        print(json.dumps(apply_curation(db,json.loads(args.manifest.read_text()),args.apply),ensure_ascii=False,indent=2))
