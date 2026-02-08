# Test Morphology Pipeline

Validate that CAMeL Tools morphological analysis is working correctly.

## Steps
1. Activate venv: `source backend/.venv/bin/activate`
2. Run `python backend/scripts/test_camel.py`
3. Check output: does each test word get the correct root, lemma, and POS?
4. Report any words that were analyzed incorrectly
5. If issues found, check if the CAMeL Tools data is installed: `camel_data -l`
