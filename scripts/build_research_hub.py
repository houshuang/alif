#!/usr/bin/env python3
"""
Build the research hub HTML from all markdown files in research/.

Walks research/ recursively, collects all .md files, extracts metadata,
builds cross-reference graph + backlinks, and generates a single self-contained
HTML file with embedded markdown rendered client-side via marked.js.

Usage:
    python3 scripts/build_research_hub.py
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
RESEARCH_DIR = ROOT_DIR / "research"
OUTPUT_FILE = RESEARCH_DIR / "research-hub.html"

# ── Category rules: slug substring → category ────────────────────────────────
CATEGORY_RULES = [
    ("learner-profile", "algorithm"),
    ("learning-algorithm-redesign", "algorithm"),
    ("deep-research-compilation", "algorithm"),
    ("experiment-log", "algorithm"),
    ("vocabulary-acquisition", "science"),
    ("cognitive-load", "science"),
    ("algorithm-implications", "science"),
    ("arabic-learning-challenges", "science"),
    ("learning-analysis", "analytics"),
    ("acquisition-rate", "analytics"),
    ("vocabulary-thresholds", "analytics"),
    ("analysis-2026", "analytics"),
    ("sentence-investigation", "generation"),
    ("story-benchmark", "generation"),
    ("corpus-vs-llm", "generation"),
    ("arabic-sentence-corpora", "generation"),
    ("arabic-morphology", "nlp"),
    ("arabic-datasets", "nlp"),
    ("arabic-diacritization", "nlp"),
    ("arabic-apis", "nlp"),
    ("arabic-learning-architecture", "nlp"),
    ("lemma-mapping", "quality"),
    ("variant-detection", "quality"),
    ("per-word-contextual", "quality"),
    ("llm-cost", "cost"),
    ("hosting", "cost"),
    ("elevenlabs", "cost"),
    ("arabic-teaching", "education"),
    ("arabic-textbook", "education"),
    ("arabic-education", "education"),
    ("arabic-children", "education"),
    ("norwegian-arabic", "education"),
    ("inn-arabic", "external"),
    ("agent-knowledge", "agent-knowledge"),
    ("research-knowledge-system", "agent-knowledge"),
]

CATEGORY_META = {
    "algorithm": {"label": "Algorithm Redesign", "color": "blue"},
    "science": {"label": "Learning Science", "color": "green"},
    "analytics": {"label": "Production Analytics", "color": "amber"},
    "generation": {"label": "Sentence & Story Gen", "color": "purple"},
    "nlp": {"label": "NLP & Infrastructure", "color": "cyan"},
    "quality": {"label": "Data Quality", "color": "red"},
    "cost": {"label": "Cost & Infrastructure", "color": "orange"},
    "education": {"label": "Arabic Education Landscape", "color": "green"},
    "external": {"label": "External References", "color": "pink"},
    "agent-knowledge": {"label": "Agent Knowledge Systems", "color": "purple"},
}

CATEGORY_ORDER = [
    "algorithm", "science", "analytics", "generation",
    "nlp", "quality", "cost", "education", "agent-knowledge", "external",
]

# ── Status overrides ─────────────────────────────────────────────────────────
STATUS_OVERRIDES = {
    "learner-profile-2026-02-12": "deployed",
    "learning-algorithm-redesign-2026-02-12": "deployed",
    "deep-research-compilation-2026-02-12": "deployed",
    "experiment-log": "active",
    "algorithm-implications": "deployed",
    "learning-analysis-2026-02-20": "deployed",
    "analysis-2026-02-09": "archived",
    "analysis-2026-02-10": "archived",
    "analysis-2026-02-11": "archived",
    "lemma-mapping-audit-2026-02-17": "deployed",
    "variant-detection-spec": "deployed",
    "per-word-contextual-translations": "todo",
    "llm-cost-investigation-2026-02-19": "deployed",
    "hosting-options": "deployed",
    "elevenlabs-arabic-voice": "deployed",
    "sentence-investigation-2026-02-13/README": "deployed",
    "sentence-investigation-2026-02-13/corpus_evaluation": "deployed",
    "sentence-investigation-2026-02-13/generation_benchmark": "deployed",
    "sentence-investigation-2026-02-13/recommendations": "deployed",
    "story-benchmark-2026-02-14/benchmark_report": "deployed",
    "story-benchmark-2026-02-14/recommendations": "deployed",
}

# ── Key findings (curated) ───────────────────────────────────────────────────
KEY_FINDINGS = [
    {
        "title": "Three-Phase Lifecycle",
        "text": "Encountered → Acquiring (Leitner 3-box) → FSRS-6. Bridges the gap between \"seen once\" and \"scheduled for review.\"",
        "color": "blue",
    },
    {
        "title": "Morphological Awareness #1",
        "text": "Root+pattern decomposition is the single strongest predictor of Arabic reading comprehension. Explicit decomposition > implicit exposure.",
        "color": "green",
    },
    {
        "title": "8–12 Encounters Needed",
        "text": "Nation's research: words need 8-12 meaningful encounters before acquisition. Sleep consolidation matters — space across days.",
        "color": "purple",
    },
    {
        "title": "85% Comprehension Sweet Spot",
        "text": "Krashen's i+1 operationalized: sessions at ~85% known words optimize the balance of challenge and acquisition.",
        "color": "amber",
    },
    {
        "title": "Sentence-Centric SRS",
        "text": "Reviewing words in sentence context is more effective than isolated flashcards. Greedy set cover maximizes due-word coverage.",
        "color": "cyan",
    },
    {
        "title": "Semantic Clustering Hurts",
        "text": "Introducing similar/related words together causes interference. Space root family members 2-4 days apart, not simultaneously.",
        "color": "red",
    },
    {
        "title": "10–20 Words/Day Max",
        "text": "Sustainable introduction rate consensus: 10-20 new words/day. The \"10x rule\" means 10 new = 100 daily reviews within weeks.",
        "color": "orange",
    },
    {
        "title": "High-Productivity Patterns",
        "text": "3 patterns show robust priming: fa'il (doer), maf'ul (object), masdar (verbal noun). Low-productivity patterns stored as whole words.",
        "color": "pink",
    },
]

# ── Timeline milestones (curated) ────────────────────────────────────────────
TIMELINE = [
    {"date": "Feb 8", "title": "Project Inception", "desc": "Hosting research, initial architecture decisions", "milestone": True},
    {"date": "Feb 9", "title": "First Production Analysis", "desc": "Day 1: 59 sentence reviews, 390 selections, 217 FSRS reviews"},
    {"date": "Feb 10", "title": "Early Metrics", "desc": "481 word reviews, 85 textbook imports, 107 words at 30d+ stability"},
    {"date": "Feb 11", "title": "Sentence Diversity Audit", "desc": "Found 24.2% of sentences start with hal. 2075 total sentences."},
    {"date": "Feb 12", "title": "Algorithm Redesign", "desc": "8-agent deep research. Three-phase lifecycle designed. Learner profile captured.", "milestone": True},
    {"date": "Feb 13", "title": "Sentence Benchmark", "desc": "213 sentences, 3 models x 6 strategies. Gemini Flash selected."},
    {"date": "Feb 14", "title": "Story Benchmark", "desc": "32 stories, 4 models x 4 strategies. Opus selected for production."},
    {"date": "Feb 17", "title": "Data Quality Crisis", "desc": "Lemma mapping audit: false al-prefix matching, 24+ wrong assignments found and fixed."},
    {"date": "Feb 19", "title": "LLM Cost Migration", "desc": "Background tasks switched to Claude CLI (free). Two-tier architecture deployed."},
    {"date": "Feb 20", "title": "Production Analytics", "desc": "13 days of data analyzed. 209 known words (28.8%), acquisition rates validated."},
    {"date": "Feb 21", "title": "Morphological Patterns", "desc": "Wazn system implemented. Pattern decomposition in UI. 83% backfill coverage.", "milestone": True},
]


# ─────────────────────────────────────────────────────────────────────────────
# Metadata extraction
# ─────────────────────────────────────────────────────────────────────────────

def slug_from_path(rel_path: str) -> str:
    """Convert relative path to slug: 'foo/bar.md' -> 'foo/bar'."""
    return rel_path.removesuffix(".md")


def extract_title(content: str, slug: str) -> str:
    """Extract title from first H1, fallback to slug."""
    m = re.search(r"^#\s+(.+)", content, re.MULTILINE)
    if m:
        title = m.group(1).strip()
        # Strip markdown links from title
        title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", title)
        # Strip bold/italic markers
        title = re.sub(r"[*_]+", "", title)
        return title
    return slug.split("/")[-1].replace("-", " ").title()


def extract_date(content: str, slug: str) -> str | None:
    """Extract date from filename (YYYY-MM-DD) or content."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", slug)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d")
            return dt.strftime("%b %d").replace(" 0", " ")
        except ValueError:
            pass
    # Fallback: look for date in content frontmatter
    m = re.search(r"(?:Date|date|Created|created):\s*(\d{4}-\d{2}-\d{2})", content)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d")
            return dt.strftime("%b %d").replace(" 0", " ")
        except ValueError:
            pass
    return None


def extract_summary(content: str) -> str:
    """Extract first non-heading, non-frontmatter paragraph, truncate to 200 chars."""
    lines = content.split("\n")
    buf = []
    in_frontmatter = False
    in_table = False
    for line in lines:
        stripped = line.strip()
        # Skip frontmatter (> ... blocks at top)
        if stripped.startswith(">"):
            in_frontmatter = True
            continue
        if in_frontmatter and not stripped:
            in_frontmatter = False
            continue
        if in_frontmatter:
            continue
        # Skip headings, horizontal rules, empty lines
        if stripped.startswith("#") or stripped.startswith("---") or not stripped:
            if buf:
                break
            continue
        # Skip tables
        if stripped.startswith("|"):
            in_table = True
            continue
        if in_table and not stripped.startswith("|"):
            in_table = False
        if in_table:
            continue
        # Skip list items at start
        if stripped.startswith("- ") or stripped.startswith("* ") or re.match(r"^\d+\.", stripped):
            if not buf:
                buf.append(re.sub(r"^[-*\d.]+\s*", "", stripped))
                continue
            else:
                break
        buf.append(stripped)
    text = " ".join(buf)
    # Clean markdown formatting
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 200:
        text = text[:197] + "..."
    return text


def extract_cross_refs(content: str, doc_slug: str) -> list[str]:
    """Extract cross-references to other .md files, resolved relative to doc."""
    refs = []
    doc_dir = os.path.dirname(doc_slug)
    for m in re.finditer(r"\[([^\]]*)\]\(([^)]+\.md)\)", content):
        target = m.group(2)
        # Strip leading ./
        target = target.removeprefix("./")
        # Resolve relative paths
        if doc_dir:
            resolved = os.path.normpath(os.path.join(doc_dir, target))
        else:
            resolved = os.path.normpath(target)
        refs.append(slug_from_path(resolved))
    return list(dict.fromkeys(refs))  # dedup preserving order


def assign_category(slug: str) -> str:
    """Assign category via pattern matching on slug."""
    for pattern, category in CATEGORY_RULES:
        if pattern in slug:
            return category
    return "uncategorized"


def assign_status(slug: str, content: str) -> str:
    """Assign status via override dict or heuristic."""
    if slug in STATUS_OVERRIDES:
        return STATUS_OVERRIDES[slug]
    lower = content.lower()
    if "deployed" in lower or "status: deployed" in lower:
        return "deployed"
    if "todo" in lower or "status: todo" in lower:
        return "todo"
    if "active" in lower or "status: active" in lower:
        return "active"
    return "reference"


# ─────────────────────────────────────────────────────────────────────────────
# Collect docs
# ─────────────────────────────────────────────────────────────────────────────

def collect_docs() -> list[dict]:
    """Walk research/ and collect all .md files with metadata."""
    docs = []
    for path in sorted(RESEARCH_DIR.rglob("*.md")):
        rel = path.relative_to(RESEARCH_DIR)
        rel_str = str(rel)

        # Skip README.md at root (redundant with hub)
        if rel_str == "README.md":
            continue

        slug = slug_from_path(rel_str)
        content = path.read_text(encoding="utf-8")

        doc = {
            "slug": slug,
            "path": f"research/{rel_str}",
            "title": extract_title(content, slug),
            "date": extract_date(content, slug),
            "summary": extract_summary(content),
            "category": assign_category(slug),
            "status": assign_status(slug, content),
            "cross_refs": extract_cross_refs(content, slug),
            "backlinks": [],  # populated later
            "content": content,
            "is_subdir_readme": rel.name == "README.md" and len(rel.parts) > 1,
            "parent_dir": str(rel.parent) if len(rel.parts) > 1 else None,
        }
        docs.append(doc)

    # Build backlinks
    slug_set = {d["slug"] for d in docs}
    for doc in docs:
        for ref_slug in doc["cross_refs"]:
            if ref_slug in slug_set:
                for target in docs:
                    if target["slug"] == ref_slug and doc["slug"] not in target["backlinks"]:
                        target["backlinks"].append(doc["slug"])

    return docs


# ─────────────────────────────────────────────────────────────────────────────
# HTML generation
# ─────────────────────────────────────────────────────────────────────────────

def build_html(docs: list[dict]) -> str:
    """Build the complete HTML string."""
    # Group docs by category
    by_category: dict[str, list[dict]] = {cat: [] for cat in CATEGORY_ORDER}
    uncategorized = []
    for doc in docs:
        cat = doc["category"]
        if cat in by_category:
            by_category[cat].append(doc)
        else:
            uncategorized.append(doc)

    # Group subdirectory docs under their README
    def sort_docs(cat_docs):
        """Sort docs: subdir READMEs first within their dir, then alpha."""
        primary = []
        sub_children = {}
        for d in cat_docs:
            if d["parent_dir"] and not d["is_subdir_readme"]:
                sub_children.setdefault(d["parent_dir"], []).append(d)
            else:
                primary.append(d)
        # For subdirs without a README, promote the first child to primary
        primary_dirs = {d["parent_dir"] for d in primary if d["parent_dir"]}
        for dir_name, children in list(sub_children.items()):
            if dir_name not in primary_dirs:
                promoted = children.pop(0)
                primary.append(promoted)
                if not children:
                    del sub_children[dir_name]
        return primary, sub_children

    # Compute KPIs
    total_docs = len(docs)
    num_categories = len([c for c in CATEGORY_ORDER if by_category.get(c)])
    dates = [d["date"] for d in docs if d["date"]]
    date_range = f"Feb 8–21" if dates else "—"

    # Unique dates from filenames for "days of research"
    date_strs = set()
    for doc in docs:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", doc["slug"])
        if m:
            date_strs.add(m.group(1))
    research_days = len(date_strs) if date_strs else "—"

    # Build docs JSON for embedding
    docs_json = {}
    for doc in docs:
        docs_json[doc["slug"]] = {
            "title": doc["title"],
            "content": doc["content"],
            "date": doc["date"],
            "category": doc["category"],
            "status": doc["status"],
            "cross_refs": doc["cross_refs"],
            "backlinks": doc["backlinks"],
            "path": doc["path"],
        }

    # Build category sections HTML
    category_sections = []
    for cat in CATEGORY_ORDER:
        cat_docs = by_category.get(cat, [])
        if not cat_docs:
            continue
        meta = CATEGORY_META[cat]
        primary, sub_children = sort_docs(cat_docs)
        rows_html = []
        for doc in primary:
            children = []
            if doc["parent_dir"]:
                children = sub_children.get(doc["parent_dir"], [])

            sub_html = ""
            if children:
                sub_items = []
                for child in children:
                    child_name = child["slug"].split("/")[-1]
                    sub_items.append(
                        f'<div class="sub-doc" onclick="showDoc(\'{_esc_attr(child["slug"])}\')" '
                        f'style="cursor:pointer">'
                        f'<code>{_esc(child_name)}.md</code> — {_esc(child["summary"][:80])}</div>'
                    )
                sub_html = f'<div class="sub-docs">{"".join(sub_items)}</div>'

            badge_class = f"badge-{doc['status']}"
            date_html = f'<span class="doc-date">{_esc(doc["date"])}</span>' if doc["date"] else ""

            rows_html.append(
                f'<div class="doc-row" data-status="{_esc_attr(doc["status"])}" '
                f'data-searchable="{_esc_attr(doc["title"] + " " + doc["summary"])}" '
                f'onclick="showDoc(\'{_esc_attr(doc["slug"])}\')" style="cursor:pointer">'
                f'  <div class="doc-icon" style="background:var(--accent-{meta["color"]}-dim);color:var(--accent-{meta["color"]})">'
                f'    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M2 1.75C2 .784 2.784 0 3.75 0h6.586c.464 0 .909.184 1.237.513l2.914 2.914c.329.328.513.773.513 1.237v9.586A1.75 1.75 0 0 1 13.25 16h-9.5A1.75 1.75 0 0 1 2 14.25Zm1.75-.25a.25.25 0 0 0-.25.25v12.5c0 .138.112.25.25.25h9.5a.25.25 0 0 0 .25-.25V6h-2.75A1.75 1.75 0 0 1 9 4.25V1.5Zm6.75.062V4.25c0 .138.112.25.25.25h2.688l-.011-.013-2.914-2.914-.013-.011Z"/></svg>'
                f'  </div>'
                f'  <div class="doc-info">'
                f'    <div class="doc-title-row">'
                f'      <span class="doc-title">{_esc(doc["title"])}</span>'
                f'      <span class="badge {badge_class}">{_esc(doc["status"].title())}</span>'
                f'      {date_html}'
                f'    </div>'
                f'    <div class="doc-summary">{_esc(doc["summary"])}</div>'
                f'    {sub_html}'
                f'    <div class="doc-path">{_esc(doc["path"])}</div>'
                f'  </div>'
                f'</div>'
            )

        category_sections.append(
            f'<section class="category-section" id="cat-{cat}" data-category="{cat}">'
            f'  <div class="category-header" onclick="toggleCategory(this)">'
            f'    <span class="category-dot" style="background:var(--accent-{meta["color"]})"></span>'
            f'    <span class="category-title">{_esc(meta["label"])}</span>'
            f'    <span class="category-count">{len(cat_docs)} docs</span>'
            f'    <svg class="category-chevron" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M12.78 6.22a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L3.22 7.28a.75.75 0 0 1 1.06-1.06L8 9.94l3.72-3.72a.75.75 0 0 1 1.06 0Z"/></svg>'
            f'  </div>'
            f'  <div class="category-body">'
            f'    {"".join(rows_html)}'
            f'  </div>'
            f'</section>'
        )

    # Build sidebar nav links
    sidebar_links = []
    for cat in CATEGORY_ORDER:
        cat_docs = by_category.get(cat, [])
        if not cat_docs:
            continue
        meta = CATEGORY_META[cat]
        sidebar_links.append(
            f'<a class="nav-link" data-nav="cat-{cat}" onclick="scrollToSection(\'cat-{cat}\')">'
            f'  <span class="dot" style="background:var(--accent-{meta["color"]})"></span>'
            f'  {_esc(meta["label"])}'
            f'  <span class="count">{len(cat_docs)}</span>'
            f'</a>'
        )

    # Build findings HTML
    findings_html = []
    for f in KEY_FINDINGS:
        findings_html.append(
            f'<div class="finding-card" style="border-color:var(--accent-{f["color"]})">'
            f'  <div class="finding-title" style="color:var(--accent-{f["color"]})">{_esc(f["title"])}</div>'
            f'  <div class="finding-text">{_esc(f["text"])}</div>'
            f'</div>'
        )

    # Build timeline HTML
    timeline_html = []
    for t in TIMELINE:
        cls = "tl-entry milestone" if t.get("milestone") else "tl-entry"
        timeline_html.append(
            f'<div class="{cls}">'
            f'  <div class="tl-date">{_esc(t["date"])}</div>'
            f'  <div class="tl-title">{_esc(t["title"])}</div>'
            f'  <div class="tl-desc">{_esc(t["desc"])}</div>'
            f'</div>'
        )

    # Mobile nav
    mobile_links = ['<a onclick="scrollToSection(\'hero\')">Dashboard</a>']
    mobile_links.append('<a onclick="scrollToSection(\'findings\')">Findings</a>')
    mobile_links.append('<a onclick="scrollToSection(\'timeline\')">Timeline</a>')
    for cat in CATEGORY_ORDER:
        if by_category.get(cat):
            meta = CATEGORY_META[cat]
            short = meta["label"].split("&")[0].split(" ")[0]
            mobile_links.append(f'<a onclick="scrollToSection(\'cat-{cat}\')">{_esc(short)}</a>')

    now = datetime.now().strftime("%B %d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Alif Research Hub</title>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Amiri:wght@400;700&display=swap" rel="stylesheet">
  <style>
{CSS}
  </style>
</head>
<body>

<div class="page-wrap">

  <!-- Sidebar -->
  <nav class="sidebar" id="sidebar" role="navigation">
    <div class="sidebar-brand" onclick="showHub()" style="cursor:pointer">
      <span class="alif">ا</span>
      <div>
        <div class="title">Research Hub</div>
        <div class="subtitle">Alif Arabic Learning</div>
      </div>
    </div>
    <div class="nav-section">
      <div class="nav-section-label">Overview</div>
      <a class="nav-link" data-nav="hero" onclick="showHub(); scrollToSection('hero')">
        <span class="dot" style="background:var(--accent-blue)"></span>
        Dashboard
      </a>
      <a class="nav-link" data-nav="findings" onclick="showHub(); scrollToSection('findings')">
        <span class="dot" style="background:var(--accent-green)"></span>
        Key Findings
      </a>
      <a class="nav-link" data-nav="timeline" onclick="showHub(); scrollToSection('timeline')">
        <span class="dot" style="background:var(--accent-purple)"></span>
        Timeline
      </a>
    </div>
    <div class="nav-section">
      <div class="nav-section-label">Categories</div>
      {"".join(sidebar_links)}
    </div>
  </nav>

  <!-- Mobile nav -->
  <div class="mobile-nav" role="navigation">
    {"".join(mobile_links)}
  </div>

  <!-- Hub view -->
  <main class="main" id="hubView">
    <section class="hero" id="hero">
      <h1>Research Hub <span class="arabic">بحث</span></h1>
      <p class="tagline">All research documents, experiments, and findings for the Alif Arabic learning project.</p>
      <div class="kpi-strip">
        <div class="kpi">
          <div class="number" style="color:var(--accent-blue)">{total_docs}</div>
          <div class="label">Research Documents</div>
        </div>
        <div class="kpi">
          <div class="number" style="color:var(--accent-green)">{num_categories}</div>
          <div class="label">Categories</div>
        </div>
        <div class="kpi">
          <div class="number" style="color:var(--accent-purple)">{research_days}</div>
          <div class="label">Days of Research</div>
        </div>
        <div class="kpi">
          <div class="number" style="color:var(--accent-amber)">{_esc(date_range)}</div>
          <div class="label">Date Range, 2026</div>
        </div>
      </div>
      <div class="search-wrap">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Zm-.82 4.74a6 6 0 1 1 1.06-1.06l3.04 3.04a.75.75 0 1 1-1.06 1.06l-3.04-3.04Z"/></svg>
        <input class="search-input" type="text" id="searchInput" placeholder="Search documents, topics, keywords..." aria-label="Search research documents">
        <span class="search-count" id="searchCount">{total_docs} docs</span>
      </div>
      <div class="filter-chips" id="filterChips">
        <button class="filter-chip active" data-filter="all">All</button>
        <button class="filter-chip" data-filter="deployed">Deployed</button>
        <button class="filter-chip" data-filter="active">Active</button>
        <button class="filter-chip" data-filter="reference">Reference</button>
        <button class="filter-chip" data-filter="todo">TODO</button>
      </div>
    </section>

    <section class="findings" id="findings">
      <h2>
        <svg width="18" height="18" viewBox="0 0 16 16" fill="var(--accent-green)"><path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM0 8a8 8 0 1 1 16 0A8 8 0 0 1 0 8Zm11.28-1.78a.75.75 0 0 0-1.06-1.06L7 8.38 5.78 7.16a.75.75 0 0 0-1.06 1.06l1.75 1.75a.75.75 0 0 0 1.06 0l3.75-3.75Z"/></svg>
        Key Research Findings
      </h2>
      <div class="findings-grid">
        {"".join(findings_html)}
      </div>
    </section>

    <section class="timeline-section" id="timeline">
      <h2>Research Timeline</h2>
      <div class="timeline">
        {"".join(timeline_html)}
      </div>
    </section>

    {"".join(category_sections)}

    <footer class="footer">
      <div>Last updated: {now}</div>
      <div>Auto-generated by <code>scripts/build_research_hub.py</code>. <a href="#" onclick="showDoc('README'); return false">README</a></div>
    </footer>
  </main>

  <!-- Document viewer -->
  <main class="main doc-viewer" id="docView" style="display:none">
    <div class="doc-nav">
      <button class="back-btn" onclick="history.back()">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M7.78 12.53a.75.75 0 0 1-1.06 0L2.47 8.28a.75.75 0 0 1 0-1.06l4.25-4.25a.751.751 0 0 1 1.042.018.751.751 0 0 1 .018 1.042L4.81 7h7.44a.75.75 0 0 1 0 1.5H4.81l2.97 2.97a.75.75 0 0 1 0 1.06Z"/></svg>
        Back to Hub
      </button>
      <span class="doc-breadcrumb" id="docBreadcrumb"></span>
    </div>
    <article class="doc-content" id="docContent"></article>
    <div class="doc-backlinks" id="docBacklinks"></div>
    <footer class="footer">
      <div>Auto-generated by <code>scripts/build_research_hub.py</code></div>
    </footer>
  </main>

</div>

<script src="https://cdn.jsdelivr.net/npm/marked@15/marked.min.js"></script>
<script>
// Embedded document data
const DOCS = {json.dumps(docs_json, ensure_ascii=False).replace("</script>", "<\\/script>")};

{JS}
</script>

</body>
</html>"""


def _esc(s: str) -> str:
    """Escape HTML entities."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _esc_attr(s: str) -> str:
    """Escape for use in HTML attributes and JS strings."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "\\'")


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────

CSS = """\
    :root {
      --bg: #0d1117;
      --bg-gradient: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #0d1117 100%);
      --surface: #161b22;
      --surface-raised: #1c2333;
      --surface-hover: #21283b;
      --border: rgba(139, 148, 158, 0.12);
      --border-accent: rgba(88, 166, 255, 0.2);
      --text: #e6edf3;
      --text-dim: #8b949e;
      --text-muted: #6e7681;
      --accent-blue: #58a6ff;
      --accent-blue-dim: rgba(88, 166, 255, 0.15);
      --accent-green: #3fb950;
      --accent-green-dim: rgba(63, 185, 80, 0.15);
      --accent-amber: #d29922;
      --accent-amber-dim: rgba(210, 153, 34, 0.15);
      --accent-purple: #bc8cff;
      --accent-purple-dim: rgba(188, 140, 255, 0.15);
      --accent-red: #f85149;
      --accent-red-dim: rgba(248, 81, 73, 0.15);
      --accent-cyan: #39d2c0;
      --accent-cyan-dim: rgba(57, 210, 192, 0.15);
      --accent-orange: #f0883e;
      --accent-orange-dim: rgba(240, 136, 62, 0.15);
      --accent-pink: #f778ba;
      --accent-pink-dim: rgba(247, 120, 186, 0.15);
      --radius: 8px;
      --radius-lg: 12px;
      --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
      --shadow-md: 0 4px 12px rgba(0,0,0,0.4);
      --shadow-lg: 0 8px 24px rgba(0,0,0,0.5);
      --font-sans: 'IBM Plex Sans', -apple-system, sans-serif;
      --font-mono: 'IBM Plex Mono', 'Menlo', monospace;
      --font-arabic: 'Amiri', serif;
    }

    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f6f8fa;
        --bg-gradient: linear-gradient(135deg, #f6f8fa 0%, #ffffff 50%, #f6f8fa 100%);
        --surface: #ffffff;
        --surface-raised: #f6f8fa;
        --surface-hover: #eef1f5;
        --border: rgba(31, 35, 40, 0.1);
        --border-accent: rgba(9, 105, 218, 0.2);
        --text: #1f2328;
        --text-dim: #656d76;
        --text-muted: #8b949e;
        --accent-blue: #0969da;
        --accent-blue-dim: rgba(9, 105, 218, 0.1);
        --accent-green: #1a7f37;
        --accent-green-dim: rgba(26, 127, 55, 0.1);
        --accent-amber: #9a6700;
        --accent-amber-dim: rgba(154, 103, 0, 0.1);
        --accent-purple: #8250df;
        --accent-purple-dim: rgba(130, 80, 223, 0.1);
        --accent-red: #cf222e;
        --accent-red-dim: rgba(207, 34, 46, 0.1);
        --accent-cyan: #0d8a7e;
        --accent-cyan-dim: rgba(13, 138, 126, 0.1);
        --accent-orange: #bc4c00;
        --accent-orange-dim: rgba(188, 76, 0, 0.1);
        --accent-pink: #bf3989;
        --accent-pink-dim: rgba(191, 57, 137, 0.1);
        --shadow-sm: 0 1px 2px rgba(0,0,0,0.06);
        --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
        --shadow-lg: 0 8px 24px rgba(0,0,0,0.12);
      }
    }

    * { margin: 0; padding: 0; box-sizing: border-box; }

    body {
      font-family: var(--font-sans);
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      min-height: 100vh;
    }

    body::before {
      content: '';
      position: fixed;
      inset: 0;
      background:
        radial-gradient(ellipse 800px 600px at 20% 10%, rgba(88, 166, 255, 0.04), transparent),
        radial-gradient(ellipse 600px 500px at 80% 80%, rgba(188, 140, 255, 0.03), transparent),
        var(--bg-gradient);
      z-index: -1;
    }

    .page-wrap { display: flex; min-height: 100vh; }

    .sidebar {
      position: sticky; top: 0; height: 100vh; width: 260px; min-width: 260px;
      padding: 24px 16px; border-right: 1px solid var(--border);
      background: var(--surface); overflow-y: auto; z-index: 10;
    }

    .sidebar-brand {
      display: flex; align-items: center; gap: 10px;
      padding: 0 8px 20px; border-bottom: 1px solid var(--border); margin-bottom: 16px;
    }
    .sidebar-brand .alif { font-family: var(--font-arabic); font-size: 28px; color: var(--accent-blue); line-height: 1; }
    .sidebar-brand .title { font-size: 14px; font-weight: 600; color: var(--text); line-height: 1.3; }
    .sidebar-brand .subtitle { font-size: 11px; color: var(--text-dim); }

    .nav-section { margin-bottom: 8px; }
    .nav-section-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); padding: 8px 8px 4px; }

    .nav-link {
      display: flex; align-items: center; gap: 8px; padding: 6px 8px;
      border-radius: 6px; font-size: 13px; color: var(--text-dim);
      text-decoration: none; cursor: pointer; transition: all 0.15s;
    }
    .nav-link:hover { background: var(--surface-hover); color: var(--text); }
    .nav-link.active { background: var(--accent-blue-dim); color: var(--accent-blue); font-weight: 500; }
    .nav-link .dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
    .nav-link .count { margin-left: auto; font-size: 11px; font-family: var(--font-mono); color: var(--text-muted); }

    .main { flex: 1; min-width: 0; padding: 32px 40px 80px; max-width: 960px; }

    .mobile-nav {
      display: none; position: sticky; top: 0; z-index: 20;
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 12px 16px; overflow-x: auto; white-space: nowrap;
    }
    .mobile-nav a {
      display: inline-block; padding: 4px 12px; border-radius: 20px;
      font-size: 12px; font-weight: 500; color: var(--text-dim);
      text-decoration: none; cursor: pointer; transition: all 0.15s;
    }
    .mobile-nav a:hover, .mobile-nav a.active { background: var(--accent-blue-dim); color: var(--accent-blue); }

    @media (max-width: 768px) {
      .sidebar { display: none; }
      .mobile-nav { display: block; }
      .main { padding: 20px 16px 60px; }
    }

    /* Hero */
    .hero { margin-bottom: 32px; }
    .hero h1 { font-size: 28px; font-weight: 700; margin-bottom: 6px; }
    .hero h1 .arabic { font-family: var(--font-arabic); color: var(--accent-blue); }
    .hero .tagline { font-size: 15px; color: var(--text-dim); margin-bottom: 20px; }

    .kpi-strip { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
    .kpi {
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
      padding: 14px 20px; min-width: 140px; flex: 1; box-shadow: var(--shadow-sm);
      animation: fadeUp 0.5s ease-out both;
    }
    .kpi:nth-child(2) { animation-delay: 0.05s; }
    .kpi:nth-child(3) { animation-delay: 0.1s; }
    .kpi:nth-child(4) { animation-delay: 0.15s; }
    .kpi .number { font-size: 28px; font-weight: 700; font-family: var(--font-mono); font-variant-numeric: tabular-nums; line-height: 1.1; }
    .kpi .label { font-size: 12px; color: var(--text-dim); margin-top: 2px; }

    .search-wrap { position: relative; margin-bottom: 28px; }
    .search-wrap svg { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--text-muted); }
    .search-input {
      width: 100%; padding: 10px 14px 10px 40px; background: var(--surface);
      border: 1px solid var(--border); border-radius: var(--radius); color: var(--text);
      font-family: var(--font-sans); font-size: 14px; outline: none; transition: border-color 0.2s;
    }
    .search-input::placeholder { color: var(--text-muted); }
    .search-input:focus { border-color: var(--accent-blue); box-shadow: 0 0 0 3px var(--accent-blue-dim); }
    .search-count { position: absolute; right: 14px; top: 50%; transform: translateY(-50%); font-size: 12px; font-family: var(--font-mono); color: var(--text-muted); }

    .filter-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 24px; }
    .filter-chip {
      padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 500;
      border: 1px solid var(--border); background: transparent; color: var(--text-dim);
      cursor: pointer; transition: all 0.15s; font-family: var(--font-sans);
    }
    .filter-chip:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
    .filter-chip.active { background: var(--accent-blue-dim); border-color: var(--accent-blue); color: var(--accent-blue); }

    /* Findings */
    .findings {
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
      padding: 24px; margin-bottom: 32px; box-shadow: var(--shadow-sm);
      animation: fadeUp 0.5s ease-out 0.2s both;
    }
    .findings h2 { font-size: 16px; font-weight: 600; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
    .findings-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
    .finding-card { padding: 12px 16px; border-radius: var(--radius); border-left: 3px solid; background: var(--surface-raised); }
    .finding-card .finding-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
    .finding-card .finding-text { font-size: 12px; color: var(--text-dim); line-height: 1.5; }

    /* Timeline */
    .timeline-section { margin-bottom: 32px; animation: fadeUp 0.5s ease-out 0.3s both; }
    .timeline-section h2 { font-size: 16px; font-weight: 600; margin-bottom: 16px; }
    .timeline { position: relative; padding: 0 0 0 24px; }
    .timeline::before {
      content: ''; position: absolute; left: 7px; top: 4px; bottom: 4px; width: 2px;
      background: linear-gradient(to bottom, var(--accent-blue), var(--accent-purple), var(--accent-green));
      border-radius: 1px;
    }
    .tl-entry { position: relative; margin-bottom: 16px; padding-left: 16px; }
    .tl-entry::before {
      content: ''; position: absolute; left: -20px; top: 8px; width: 10px; height: 10px;
      border-radius: 50%; background: var(--surface); border: 2px solid var(--accent-blue);
    }
    .tl-entry.milestone::before { width: 12px; height: 12px; left: -21px; background: var(--accent-blue); box-shadow: 0 0 8px var(--accent-blue-dim); }
    .tl-date { font-size: 12px; font-family: var(--font-mono); color: var(--accent-blue); font-weight: 500; }
    .tl-title { font-size: 13px; font-weight: 500; }
    .tl-desc { font-size: 12px; color: var(--text-dim); }

    /* Category sections */
    .category-section { margin-bottom: 24px; animation: fadeUp 0.5s ease-out both; }
    .category-header {
      display: flex; align-items: center; gap: 10px; padding: 12px 16px;
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius-lg) var(--radius-lg) 0 0;
      cursor: pointer; user-select: none; transition: background 0.15s;
    }
    .category-header:hover { background: var(--surface-hover); }
    .category-section.collapsed .category-header { border-radius: var(--radius-lg); }
    .category-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
    .category-title { font-size: 15px; font-weight: 600; flex: 1; }
    .category-count { font-size: 12px; font-family: var(--font-mono); color: var(--text-muted); background: var(--surface-raised); padding: 2px 8px; border-radius: 10px; }
    .category-chevron { color: var(--text-muted); transition: transform 0.2s; }
    .category-section.collapsed .category-chevron { transform: rotate(-90deg); }
    .category-body { border: 1px solid var(--border); border-top: none; border-radius: 0 0 var(--radius-lg) var(--radius-lg); overflow: hidden; }
    .category-section.collapsed .category-body { display: none; }

    /* Doc rows */
    .doc-row {
      display: flex; align-items: flex-start; gap: 12px; padding: 12px 16px;
      border-bottom: 1px solid var(--border); transition: background 0.15s;
    }
    .doc-row:last-child { border-bottom: none; }
    .doc-row:hover { background: var(--surface-hover); }
    .doc-row.hidden { display: none; }

    .doc-icon { width: 32px; height: 32px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0; margin-top: 2px; }
    .doc-info { flex: 1; min-width: 0; }
    .doc-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .doc-title { font-size: 14px; font-weight: 500; }
    .doc-date { font-size: 11px; font-family: var(--font-mono); color: var(--text-muted); }
    .doc-summary { font-size: 12px; color: var(--text-dim); margin-top: 3px; line-height: 1.5; }
    .doc-path { font-size: 11px; font-family: var(--font-mono); color: var(--text-muted); margin-top: 4px; opacity: 0.7; }

    .badge { display: inline-flex; align-items: center; gap: 4px; padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; flex-shrink: 0; white-space: nowrap; }
    .badge-deployed { background: var(--accent-green-dim); color: var(--accent-green); }
    .badge-active { background: var(--accent-blue-dim); color: var(--accent-blue); }
    .badge-reference { background: var(--accent-purple-dim); color: var(--accent-purple); }
    .badge-todo { background: var(--accent-amber-dim); color: var(--accent-amber); }
    .badge-archived { background: var(--surface-raised); color: var(--text-muted); }
    .badge::before { content: ''; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }

    .sub-docs { margin-top: 6px; padding-left: 12px; border-left: 2px solid var(--border); }
    .sub-doc { font-size: 12px; color: var(--text-dim); padding: 3px 0; }
    .sub-doc:hover { color: var(--accent-blue); }
    .sub-doc code { font-family: var(--font-mono); font-size: 11px; background: var(--surface-raised); padding: 1px 5px; border-radius: 3px; }

    .footer {
      margin-top: 48px; padding-top: 24px; border-top: 1px solid var(--border);
      font-size: 12px; color: var(--text-muted); text-align: center; line-height: 1.8;
    }
    .footer a { color: var(--accent-blue); text-decoration: none; }
    .footer code { font-family: var(--font-mono); font-size: 11px; background: var(--surface-raised); padding: 1px 5px; border-radius: 3px; }

    @keyframes fadeUp {
      from { opacity: 0; transform: translateY(12px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
    }

    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

    /* ====== Document viewer ====== */
    .doc-viewer { display: none; }

    .doc-nav {
      display: flex; align-items: center; gap: 12px;
      margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
    }
    .back-btn {
      display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px;
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
      color: var(--text-dim); font-family: var(--font-sans); font-size: 13px;
      cursor: pointer; transition: all 0.15s;
    }
    .back-btn:hover { background: var(--surface-hover); color: var(--text); border-color: var(--accent-blue); }

    .doc-breadcrumb { font-size: 12px; color: var(--text-muted); font-family: var(--font-mono); }

    /* Article typography */
    .doc-content {
      line-height: 1.7; font-size: 15px;
    }
    .doc-content h1 { font-size: 26px; font-weight: 700; margin: 0 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }
    .doc-content h2 { font-size: 20px; font-weight: 600; margin: 32px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
    .doc-content h3 { font-size: 17px; font-weight: 600; margin: 24px 0 8px; }
    .doc-content h4 { font-size: 15px; font-weight: 600; margin: 20px 0 6px; }
    .doc-content p { margin: 0 0 12px; }
    .doc-content ul, .doc-content ol { margin: 0 0 12px; padding-left: 24px; }
    .doc-content li { margin-bottom: 4px; }
    .doc-content a { color: var(--accent-blue); text-decoration: none; }
    .doc-content a:hover { text-decoration: underline; }
    .doc-content a.internal-ref { border-bottom: 1.5px dotted var(--accent-blue); text-decoration: none; }
    .doc-content a.internal-ref:hover { border-bottom-style: solid; }

    .doc-content blockquote {
      margin: 0 0 12px; padding: 8px 16px; border-left: 3px solid var(--accent-blue);
      background: var(--surface-raised); border-radius: 0 var(--radius) var(--radius) 0;
      color: var(--text-dim); font-size: 14px;
    }
    .doc-content blockquote p:last-child { margin-bottom: 0; }

    .doc-content code {
      font-family: var(--font-mono); font-size: 0.88em;
      background: var(--surface-raised); padding: 2px 6px; border-radius: 4px;
    }
    .doc-content pre {
      margin: 0 0 12px; padding: 16px; background: var(--surface-raised);
      border: 1px solid var(--border); border-radius: var(--radius);
      overflow-x: auto; font-size: 13px; line-height: 1.5;
    }
    .doc-content pre code { background: none; padding: 0; border-radius: 0; font-size: inherit; }

    .doc-content table {
      width: 100%; margin: 0 0 12px; border-collapse: collapse;
      font-size: 13px;
    }
    .doc-content th, .doc-content td {
      padding: 8px 12px; border: 1px solid var(--border); text-align: left;
    }
    .doc-content th { background: var(--surface-raised); font-weight: 600; }
    .doc-content tr:hover td { background: var(--surface-hover); }

    .doc-content hr { border: none; border-top: 1px solid var(--border); margin: 24px 0; }
    .doc-content img { max-width: 100%; border-radius: var(--radius); }
    .doc-content strong { font-weight: 600; }

    /* Arabic text detection */
    .doc-content :lang(ar), .doc-content [dir="rtl"] { font-family: var(--font-arabic); }

    /* Backlinks */
    .doc-backlinks {
      margin-top: 32px; padding: 16px; background: var(--surface);
      border: 1px solid var(--border); border-radius: var(--radius-lg);
    }
    .doc-backlinks h3 { font-size: 14px; font-weight: 600; margin-bottom: 8px; color: var(--text-dim); }
    .doc-backlinks a {
      display: inline-block; margin: 2px 4px; padding: 2px 10px;
      background: var(--surface-raised); border-radius: 12px;
      font-size: 12px; color: var(--accent-blue); text-decoration: none;
      border: 1px solid var(--border); transition: all 0.15s;
    }
    .doc-backlinks a:hover { background: var(--accent-blue-dim); border-color: var(--accent-blue); }
    .doc-backlinks.empty { display: none; }"""


# ─────────────────────────────────────────────────────────────────────────────
# JavaScript
# ─────────────────────────────────────────────────────────────────────────────

JS = """\
// ====== Basic markdown renderer (fallback when marked.js CDN unavailable) ======
function renderMarkdownBasic(md) {
  let h = escapeHtml(md);
  // Code blocks (``` ... ```)
  h = h.replace(/```(\\w*)\\n([\\s\\S]*?)```/g, function(_, lang, code) {
    return '<pre><code>' + code + '</code></pre>';
  });
  // Inline code
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Headings
  h = h.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
  h = h.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  h = h.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  h = h.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // Bold and italic
  h = h.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
  h = h.replace(/\\*([^*]+)\\*/g, '<em>$1</em>');
  // Links
  h = h.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2">$1</a>');
  // Blockquotes
  h = h.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
  // Horizontal rules
  h = h.replace(/^---+$/gm, '<hr>');
  // Unordered lists
  h = h.replace(/^- (.+)$/gm, '<li>$1</li>');
  h = h.replace(/(<li>.*<\\/li>\\n?)+/g, '<ul>$&</ul>');
  // Tables (basic)
  h = h.replace(/^\\|(.+)\\|$/gm, function(line) {
    if (line.match(/^\\|[\\s-:|]+\\|$/)) return '';
    const cells = line.split('|').filter(c => c.trim());
    const tag = 'td';
    return '<tr>' + cells.map(c => '<' + tag + '>' + c.trim() + '</' + tag + '>').join('') + '</tr>';
  });
  h = h.replace(/(<tr>.*<\\/tr>\\n?)+/g, '<table>$&</table>');
  // Paragraphs
  h = h.replace(/\\n\\n+/g, '</p><p>');
  h = '<p>' + h + '</p>';
  // Clean up empty paragraphs
  h = h.replace(/<p>\\s*<\\/p>/g, '');
  h = h.replace(/<p>\\s*(<h[1-4]>)/g, '$1');
  h = h.replace(/(<\\/h[1-4]>)\\s*<\\/p>/g, '$1');
  h = h.replace(/<p>\\s*(<pre>)/g, '$1');
  h = h.replace(/(<\\/pre>)\\s*<\\/p>/g, '$1');
  h = h.replace(/<p>\\s*(<ul>)/g, '$1');
  h = h.replace(/(<\\/ul>)\\s*<\\/p>/g, '$1');
  h = h.replace(/<p>\\s*(<table>)/g, '$1');
  h = h.replace(/(<\\/table>)\\s*<\\/p>/g, '$1');
  h = h.replace(/<p>\\s*(<hr>)/g, '$1');
  h = h.replace(/(<hr>)\\s*<\\/p>/g, '$1');
  h = h.replace(/<p>\\s*(<blockquote>)/g, '$1');
  h = h.replace(/(<\\/blockquote>)\\s*<\\/p>/g, '$1');
  return h;
}

// ====== Hash routing ======
function showHub() {
  if (location.hash.startsWith('#doc:')) {
    location.hash = '';
  }
  document.getElementById('hubView').style.display = 'block';
  document.getElementById('docView').style.display = 'none';
  document.title = 'Alif Research Hub';
}

function showDoc(slug) {
  const doc = DOCS[slug];
  if (!doc) {
    // Try partial match (e.g. README -> first README match)
    const match = Object.keys(DOCS).find(k => k.endsWith('/' + slug) || k === slug);
    if (match) return showDoc(match);
    console.warn('Doc not found:', slug);
    return;
  }

  location.hash = 'doc:' + slug;

  document.getElementById('hubView').style.display = 'none';
  document.getElementById('docView').style.display = 'block';

  // Breadcrumb
  const cat = doc.category;
  const catMeta = {
    algorithm: 'Algorithm Redesign', science: 'Learning Science',
    analytics: 'Production Analytics', generation: 'Sentence & Story Gen',
    nlp: 'NLP & Infrastructure', quality: 'Data Quality',
    cost: 'Cost & Infrastructure', education: 'Arabic Education',
    external: 'External References'
  };
  document.getElementById('docBreadcrumb').textContent =
    (catMeta[cat] || cat) + ' / ' + doc.title;

  // Render markdown
  let html = '';
  if (typeof marked !== 'undefined') {
    html = marked.parse(doc.content);
  } else {
    html = renderMarkdownBasic(doc.content);
  }

  // Rewrite internal .md links to showDoc() calls
  html = html.replace(
    /href="([^"]*\\.md)"/g,
    function(match, href) {
      // Resolve relative paths
      let target = href.replace(/^\\.\\//,'');
      const docDir = slug.includes('/') ? slug.substring(0, slug.lastIndexOf('/')) : '';
      if (docDir && !target.includes('/')) {
        target = docDir + '/' + target;
      }
      // Normalize ../
      const parts = target.split('/');
      const resolved = [];
      for (const p of parts) {
        if (p === '..') resolved.pop();
        else if (p !== '.') resolved.push(p);
      }
      target = resolved.join('/').replace(/\\.md$/, '');
      if (DOCS[target]) {
        return 'href="#doc:' + target + '" class="internal-ref" onclick="showDoc(\\'' + target.replace(/'/g, "\\\\'") + '\\'); return false;"';
      }
      return match;
    }
  );

  document.getElementById('docContent').innerHTML = html;
  document.title = doc.title + ' — Alif Research Hub';

  // Backlinks
  const bl = document.getElementById('docBacklinks');
  if (doc.backlinks && doc.backlinks.length > 0) {
    bl.classList.remove('empty');
    let blHtml = '<h3>Referenced by ' + doc.backlinks.length + ' document' + (doc.backlinks.length > 1 ? 's' : '') + '</h3>';
    for (const ref of doc.backlinks) {
      const refDoc = DOCS[ref];
      if (refDoc) {
        blHtml += '<a href="#doc:' + ref + '" onclick="showDoc(\\'' + ref.replace(/'/g, "\\\\'") + '\\'); return false;">' + escapeHtml(refDoc.title) + '</a>';
      }
    }
    bl.innerHTML = blHtml;
  } else {
    bl.classList.add('empty');
    bl.innerHTML = '';
  }

  // Highlight active sidebar category
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  const catNav = document.querySelector('[data-nav="cat-' + cat + '"]');
  if (catNav) catNav.classList.add('active');

  window.scrollTo(0, 0);
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Hash change listener
window.addEventListener('hashchange', function() {
  const hash = location.hash;
  if (hash.startsWith('#doc:')) {
    showDoc(hash.substring(5));
  } else {
    showHub();
  }
});

// Initial route
if (location.hash.startsWith('#doc:')) {
  showDoc(location.hash.substring(5));
}

// ====== Section scrolling ======
function scrollToSection(id) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ====== Category toggle ======
function toggleCategory(header) {
  header.parentElement.classList.toggle('collapsed');
}

// ====== Search ======
const searchInput = document.getElementById('searchInput');
const searchCount = document.getElementById('searchCount');

searchInput.addEventListener('input', function() {
  const query = this.value.toLowerCase().trim();
  const rows = document.querySelectorAll('#hubView .doc-row');
  let visible = 0;

  if (!query) {
    // Also search in embedded content
    rows.forEach(row => { row.classList.remove('hidden'); visible++; });
  } else {
    // Search in displayed text AND in embedded markdown content
    rows.forEach(row => {
      const searchable = (row.dataset.searchable || '') + ' ' +
        (row.querySelector('.doc-title')?.textContent || '') + ' ' +
        (row.querySelector('.doc-summary')?.textContent || '') + ' ' +
        (row.querySelector('.doc-path')?.textContent || '');

      // Also search embedded content via slug from onclick
      const onclick = row.getAttribute('onclick') || '';
      const slugMatch = onclick.match(/showDoc\\('([^']+)'\\)/);
      let contentMatch = false;
      if (slugMatch && DOCS[slugMatch[1]]) {
        contentMatch = DOCS[slugMatch[1]].content.toLowerCase().includes(query);
      }

      if (searchable.toLowerCase().includes(query) || contentMatch) {
        row.classList.remove('hidden');
        visible++;
      } else {
        row.classList.add('hidden');
      }
    });
  }

  searchCount.textContent = visible + ' doc' + (visible !== 1 ? 's' : '');

  if (query) {
    document.querySelectorAll('.category-section').forEach(section => {
      const hasVisible = section.querySelector('.doc-row:not(.hidden)');
      if (hasVisible) section.classList.remove('collapsed');
      else section.classList.add('collapsed');
    });
  }
});

// ====== Filter chips ======
document.querySelectorAll('.filter-chip').forEach(chip => {
  chip.addEventListener('click', function() {
    document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
    this.classList.add('active');
    const filter = this.dataset.filter;
    const rows = document.querySelectorAll('#hubView .doc-row');
    let visible = 0;
    rows.forEach(row => {
      if (filter === 'all' || row.dataset.status === filter) {
        row.classList.remove('hidden');
        visible++;
      } else {
        row.classList.add('hidden');
      }
    });
    searchCount.textContent = visible + ' doc' + (visible !== 1 ? 's' : '');
    searchInput.value = '';
    document.querySelectorAll('.category-section').forEach(section => {
      const hasVisible = section.querySelector('.doc-row:not(.hidden)');
      if (hasVisible) section.classList.remove('collapsed');
      else if (filter !== 'all') section.classList.add('collapsed');
    });
  });
});

// ====== Scroll spy ======
function updateActiveNav() {
  if (document.getElementById('hubView').style.display === 'none') return;
  let current = '';
  document.querySelectorAll('[id]').forEach(section => {
    const rect = section.getBoundingClientRect();
    if (rect.top <= 100) current = section.id;
  });
  document.querySelectorAll('.nav-link[data-nav]').forEach(link => {
    link.classList.toggle('active', link.dataset.nav === current);
  });
}
window.addEventListener('scroll', updateActiveNav, { passive: true });
updateActiveNav();"""


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    docs = collect_docs()

    # Report
    categories = {}
    uncategorized = []
    for doc in docs:
        if doc["category"] == "uncategorized":
            uncategorized.append(doc["slug"])
        categories.setdefault(doc["category"], []).append(doc["slug"])

    print(f"Collected {len(docs)} documents across {len(categories)} categories")
    for cat in CATEGORY_ORDER:
        if cat in categories:
            print(f"  {cat}: {len(categories[cat])} docs")
    if uncategorized:
        print(f"\n  WARNING: {len(uncategorized)} uncategorized docs:")
        for slug in uncategorized:
            print(f"    - {slug}")

    # Cross-ref stats
    total_refs = sum(len(d["cross_refs"]) for d in docs)
    total_backlinks = sum(len(d["backlinks"]) for d in docs)
    print(f"\nCross-references: {total_refs} outgoing, {total_backlinks} backlinks")

    html = build_html(docs)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    size_kb = len(html.encode("utf-8")) / 1024
    print(f"\nWrote {OUTPUT_FILE} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
