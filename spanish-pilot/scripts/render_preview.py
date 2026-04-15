"""Render calibration content as a single HTML file for human review."""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEMMAS_PATH = ROOT / "content" / "lemmas.json"
SENTENCES_PATH = ROOT / "content" / "sentences.json"
OUT_PATH = ROOT / "content" / "preview.html"


def esc(s: str) -> str:
    return html.escape(str(s) if s is not None else "")


def render_lemma_card(l: dict) -> str:
    conj = ""
    if l.get("conjugation_applicable"):
        c = l["conjugation_present"]
        conj = f"""<div class="conj-box">
          <div class="conj-title">Presens</div>
          <table class="conj">
            <tr><td>yo</td><td><strong>{esc(c['yo'])}</strong></td><td>nosotros</td><td><strong>{esc(c['nosotros'])}</strong></td></tr>
            <tr><td>tú</td><td><strong>{esc(c['tu'])}</strong></td><td>vosotros</td><td><strong>{esc(c['vosotros'])}</strong></td></tr>
            <tr><td>él/ella</td><td><strong>{esc(c['el'])}</strong></td><td>ellos/ellas</td><td><strong>{esc(c['ellos'])}</strong></td></tr>
          </table>
        </div>"""

    agr = ""
    af = l.get("agreement_forms", {})
    if any(af.values()):
        agr = f"""<div class="conj-box">
          <div class="conj-title">Kongruens</div>
          <table class="conj">
            <tr><td>mask.sg</td><td><strong>{esc(af.get('masc_sg'))}</strong></td><td>mask.pl</td><td><strong>{esc(af.get('masc_pl'))}</strong></td></tr>
            <tr><td>fem.sg</td><td><strong>{esc(af.get('fem_sg'))}</strong></td><td>fem.pl</td><td><strong>{esc(af.get('fem_pl'))}</strong></td></tr>
          </table>
        </div>"""

    plural = f"<div><span class='lbl'>Flertall:</span> <strong>{esc(l['plural_form'])}</strong></div>" if l.get("plural_form") else ""
    quirk = f"<div class='quirk'><span class='lbl'>Særegenhet:</span> {esc(l['article_quirk'])}</div>" if l.get("article_quirk") else ""
    gender_badge = ""
    if l.get("gender") and l["gender"] != "none":
        gender_badge = f"<span class='badge badge-{l['gender']}'>{'m' if l['gender']=='m' else 'f' if l['gender']=='f' else l['gender']}</span>"

    return f"""<div class="card lemma-card">
  <div class="lemma-head">
    <div class="lemma-es">{esc(l['lemma_es'])}</div>
    <div class="lemma-gloss">{esc(l['gloss_no'])}</div>
    <div class="lemma-meta">
      <span class="pos">{esc(l['pos'])}</span>
      {gender_badge}
      <span class="cefr">{esc(l['cefr_level'])}</span>
      <span class="freq">rank ~{esc(l['frequency_rank_estimate'])}</span>
    </div>
  </div>
  {plural}
  {quirk}
  {conj}
  {agr}
  <div class="hook"><span class="lbl">Memory hook:</span> {esc(l['memory_hook_no'])}</div>
  <div class="etym"><span class="lbl">Etymologi:</span> {esc(l['etymology_no'])}</div>
  <div class="example">
    <div class="ex-es">{esc(l['example_es'])}</div>
    <div class="ex-no">{esc(l['example_no'])}</div>
  </div>
</div>"""


def render_sentence_card(s: dict, idx: int, issues_for_s: list[dict]) -> str:
    # Build word mapping display
    wm_rows = "".join(
        f"<tr><td class='pos-num'>{w['position']}</td><td><strong>{esc(w['form'])}</strong></td><td>→</td><td>{esc(w['lemma_es'])}</td><td class='gram'>{esc(w['grammatical_note'])}</td></tr>"
        for w in sorted(s["word_mapping"], key=lambda x: x["position"])
    )
    distractors = "".join(
        f"<li>{esc(d)}</li>" for d in s["distractors_no"]
    )
    issues_html = ""
    if issues_for_s:
        rows = "".join(
            f"<li class='sev-{iss['severity']}'><strong>{iss['severity'].upper()}</strong> ({iss['field']}): {esc(iss['problem_no'])}<br><em>Forslag:</em> {esc(iss['suggested_fix_no'])}</li>"
            for iss in issues_for_s
        )
        issues_html = f"<div class='issues'><div class='lbl'>Verifikasjon flagget:</div><ul>{rows}</ul></div>"

    return f"""<div class="card sentence-card">
  <div class="sent-head"><span class="s-idx">{idx}</span></div>
  <div class="sent-es">{esc(s['es'])}</div>
  <div class="sent-no">{esc(s['no'])}</div>
  <details><summary>Ordanalyse</summary>
    <table class="wm">{wm_rows}</table>
  </details>
  <details><summary>Flervalg-distractors (feil svar)</summary>
    <ul class="distractors">{distractors}</ul>
  </details>
  {issues_html}
</div>"""


def main() -> None:
    lemmas_data = json.loads(LEMMAS_PATH.read_text())
    lemmas = lemmas_data.get("enriched_corrected", lemmas_data["enriched"])["lemmas"]
    lemma_issues = lemmas_data.get("verification_issues", [])

    sent_data = json.loads(SENTENCES_PATH.read_text())
    sents = sent_data.get("sentences_corrected", sent_data["sentences"])["sentences"]
    sent_issues = sent_data.get("verification_issues", [])

    lemma_cards = "\n".join(render_lemma_card(l) for l in lemmas)

    sent_cards_list = []
    for i, s in enumerate(sents):
        issues_for_s = [iss for iss in sent_issues if iss.get("sentence_index") == i]
        sent_cards_list.append(render_sentence_card(s, i, issues_for_s))
    sent_cards = "\n".join(sent_cards_list)

    lemma_issue_html = ""
    if lemma_issues:
        real_issues = [
            iss for iss in lemma_issues
            if not (iss["field"].startswith("conjugation_present.")
                    and "aksent" in iss["problem_no"].lower())
        ]
        if real_issues:
            rows = "".join(
                f"<li class='sev-{iss['severity']}'><strong>{iss['severity'].upper()}</strong> — <code>{esc(iss['lemma_es'])}.{esc(iss['field'])}</code>: {esc(iss['problem_no'])}<br><em>Fix:</em> {esc(iss['suggested_fix_no'])}</li>"
                for iss in real_issues
            )
            lemma_issue_html = f"""<div class="issue-log">
  <h3>Lemma-verifikasjon fanget {len(real_issues)} problemer (alle løst i korrigert versjon)</h3>
  <ul>{rows}</ul>
</div>"""

    sent_issue_html = ""
    if sent_issues:
        rows = "".join(
            f"<li class='sev-{iss['severity']}'><strong>{iss['severity'].upper()}</strong> — s{iss['sentence_index']}.{iss['field']}: {esc(iss['problem_no'])}<br><em>Fix:</em> {esc(iss['suggested_fix_no'])}</li>"
            for iss in sent_issues
        )
        sent_issue_html = f"""<div class="issue-log">
  <h3>Setning-verifikasjon fanget {len(sent_issues)} problemer (løst i korrigert versjon)</h3>
  <ul>{rows}</ul>
</div>"""

    html_out = f"""<!doctype html>
<html lang="no">
<head>
<meta charset="utf-8">
<title>Spanish Pilot — Kalibreringsforhåndsvisning</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1100px; margin: 0 auto; padding: 2rem; color: #1a1a1a; background: #fafaf8; }}
  h1 {{ font-size: 2rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.4rem; border-bottom: 2px solid #2a6496; padding-bottom: 0.25rem; margin-top: 3rem; }}
  .intro {{ background: #fff8e0; border-left: 4px solid #d4a942; padding: 1rem 1.25rem; margin: 1.5rem 0; border-radius: 4px; }}
  .stats {{ display: flex; gap: 2rem; margin: 1rem 0 2rem; }}
  .stat {{ font-size: 0.9rem; color: #555; }}
  .stat strong {{ font-size: 1.4rem; color: #1a1a1a; display: block; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 1rem; }}
  .card {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 1rem 1.25rem; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
  .lemma-card .lemma-head {{ border-bottom: 1px solid #eee; padding-bottom: 0.6rem; margin-bottom: 0.6rem; }}
  .lemma-es {{ font-size: 1.5rem; font-weight: bold; color: #2a6496; }}
  .lemma-gloss {{ font-size: 1.05rem; color: #444; font-style: italic; margin-top: 0.15rem; }}
  .lemma-meta {{ font-size: 0.8rem; color: #666; margin-top: 0.25rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }}
  .pos {{ background: #edf2f7; padding: 0.1rem 0.4rem; border-radius: 3px; }}
  .badge {{ padding: 0.1rem 0.4rem; border-radius: 3px; font-weight: bold; font-size: 0.75rem; }}
  .badge-m {{ background: #cfe2f3; color: #1a4e8a; }}
  .badge-f {{ background: #f4cccc; color: #8a1a3b; }}
  .cefr {{ background: #d9ead3; color: #365d2c; padding: 0.1rem 0.4rem; border-radius: 3px; }}
  .freq {{ color: #888; }}
  .conj-box {{ margin: 0.5rem 0; padding: 0.5rem 0.6rem; background: #f7f7f2; border-radius: 4px; }}
  .conj-title {{ font-weight: bold; color: #555; font-size: 0.85rem; margin-bottom: 0.3rem; }}
  .conj {{ border-collapse: collapse; font-size: 0.9rem; width: 100%; }}
  .conj td {{ padding: 0.12rem 0.4rem; }}
  .conj td:first-child, .conj td:nth-child(3) {{ color: #888; }}
  .hook, .etym {{ margin: 0.5rem 0; font-size: 0.92rem; line-height: 1.45; }}
  .quirk {{ background: #fff3cd; border-left: 3px solid #d4a942; padding: 0.4rem 0.6rem; margin: 0.5rem 0; font-size: 0.88rem; border-radius: 3px; }}
  .lbl {{ font-weight: bold; color: #555; font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.02em; }}
  .example {{ margin-top: 0.7rem; padding-top: 0.5rem; border-top: 1px dashed #ddd; }}
  .ex-es {{ font-size: 1rem; color: #1a4e8a; }}
  .ex-no {{ font-size: 0.92rem; color: #666; font-style: italic; }}

  .sentence-card {{ position: relative; }}
  .s-idx {{ position: absolute; top: 0.5rem; right: 0.75rem; color: #bbb; font-size: 0.78rem; }}
  .sent-es {{ font-size: 1.15rem; color: #1a4e8a; font-weight: 500; }}
  .sent-no {{ font-size: 1rem; color: #444; margin: 0.3rem 0 0.7rem; }}
  details {{ margin: 0.3rem 0; font-size: 0.88rem; }}
  details summary {{ cursor: pointer; color: #666; }}
  .wm {{ border-collapse: collapse; margin-top: 0.4rem; font-size: 0.88rem; width: 100%; }}
  .wm td {{ padding: 0.1rem 0.35rem; }}
  .wm .pos-num {{ color: #aaa; font-variant-numeric: tabular-nums; }}
  .wm .gram {{ color: #888; font-size: 0.8rem; }}
  .distractors {{ margin: 0.3rem 0 0; padding-left: 1.2rem; font-size: 0.9rem; color: #8a1a3b; }}
  .distractors li {{ margin: 0.15rem 0; }}
  .issues {{ margin-top: 0.7rem; background: #fdecea; border-left: 3px solid #c0392b; padding: 0.5rem 0.6rem; border-radius: 3px; font-size: 0.85rem; }}
  .issues ul {{ padding-left: 1.2rem; margin: 0.3rem 0 0; }}
  .sev-critical {{ color: #c0392b; }}
  .sev-minor {{ color: #d4a942; }}
  .sev-stylistic {{ color: #888; }}
  .issue-log {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 1rem 1.25rem; margin-top: 1.5rem; }}
  .issue-log h3 {{ margin: 0 0 0.5rem; font-size: 1rem; }}
  .issue-log ul {{ margin: 0; padding-left: 1.2rem; font-size: 0.88rem; }}
  .issue-log li {{ margin: 0.3rem 0; }}
  code {{ background: #eee; padding: 0.05rem 0.3rem; border-radius: 2px; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>Spanish Pilot — Kalibreringsforhåndsvisning</h1>
<p style="color:#666; margin-top:0;">Liten kalibreringsbatch med {len(lemmas)} lemmaer og {len(sents)} setninger. Kvalitet du ser her vil skaleres til ~100 lemmaer + ~150 setninger etter godkjenning.</p>

<div class="intro">
  <strong>Hva du bør se etter:</strong>
  <ul style="margin: 0.5rem 0 0; padding-left: 1.4rem;">
    <li>Er memory hooks og etymologi <em>genuint nyttige</em> for norske elever? (ikke banale/generiske)</li>
    <li>Er oversettelsene <em>idiomatiske norske</em> setninger, ikke ord-for-ord?</li>
    <li>Stemmer konjugasjonstabellene og kongruensformene helt?</li>
    <li>Er distractors (flervalg-alternativer) <em>plausible feil</em> som ville lure en elev, ikke tilfeldige setninger?</li>
    <li>Er ordanalysen (surface form → lemma) riktig for hver setning?</li>
    <li>Føles særegenheter som «el agua» (maskulin artikkel, feminin substantiv) godt forklart?</li>
  </ul>
  <p style="margin: 0.7rem 0 0;"><strong>Hvis noe ser galt ut:</strong> flagg det og send tilbake. Hvis kvaliteten er god nok for et pilot, gir du klarsignal for å skalere opp.</p>
</div>

<div class="stats">
  <div class="stat"><strong>{len(lemmas)}</strong>lemmaer</div>
  <div class="stat"><strong>{len(sents)}</strong>setninger</div>
  <div class="stat"><strong>{len(lemma_issues)}</strong>lemma-issues (fikset)</div>
  <div class="stat"><strong>{len(sent_issues)}</strong>setning-issues (fikset)</div>
</div>

<h2>Lemmaer (ordinformasjon + detaljkort)</h2>
<p style="color:#666; font-size: 0.9rem;">Hvert kort er det som vises i appens «ord-detaljvisning» når eleven trykker på et ord i en setning.</p>
<div class="grid">{lemma_cards}</div>

{lemma_issue_html}

<h2>Setninger (øvingsmateriell)</h2>
<p style="color:#666; font-size: 0.9rem;">Hver setning har spansk tekst, norsk oversettelse, ordanalyse (hvilket ord er hvilken lemma), og 3 distractors for flervalg-modus.</p>
<div class="grid">{sent_cards}</div>

{sent_issue_html}

<h2>Neste steg</h2>
<ol>
  <li>Du gjennomgår denne siden</li>
  <li>Hvis kvalitet OK → jeg skalerer til ~100 lemmaer + ~150 setninger</li>
  <li>Jeg bygger backend (FSRS + Leitner 3-box, SQLite, API)</li>
  <li>Jeg bygger frontend (norsk UI, elevliste-login, selvvurdering + flervalg, dashboard med Leitner-bokser)</li>
  <li>Deploy til Hetzner port 3100</li>
  <li>Du deler URL med lærer</li>
</ol>

</body>
</html>"""

    OUT_PATH.write_text(html_out)
    print(f"Wrote {OUT_PATH}")
    print(f"Open with: open {OUT_PATH}")


if __name__ == "__main__":
    main()
