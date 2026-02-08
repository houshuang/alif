# Import Word List

Import a word list (CSV or JSON) into the Alif database.

## Steps
1. Activate venv: `source backend/.venv/bin/activate`
2. Determine the input format (CSV with arabic,english columns or Duolingo JSON)
3. Run the appropriate import script:
   - Duolingo: `python backend/scripts/import_duolingo.py`
   - CSV: `python backend/scripts/import_csv.py <path>`
4. Verify import: `curl http://localhost:8000/api/words?per_page=10`
5. Report: words imported, words skipped, any errors
