#!/usr/bin/env python3
"""Preview automatic priorities; --apply changes only attention metadata."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.database import SessionLocal
from app.services.automatic_attention import refresh_attention

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        print(json.dumps(refresh_attention(db, apply=args.apply), ensure_ascii=False, indent=2))
