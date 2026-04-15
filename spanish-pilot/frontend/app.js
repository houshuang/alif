// Spansk — norsk pilot. Vanilla JS. Mirrors Alif's sentence-first review UX.

const API = "";
const app = document.getElementById("app");

const FUNC_POS = new Set(["article", "preposition", "conjunction"]);

const state = {
  students: [],
  currentStudent: null,
  sessionItems: [],
  sessionIdx: 0,
  sessionMode: "self_grade",
  sessionStats: { correct: 0, wrong: 0, new_intro: 0 },
  itemStart: 0,

  // Per-item review state
  showTranslation: false,
  expandedWord: null,        // lemma_es of currently-expanded word
  markedMissed: new Set(),   // lemma_es set marked as "missed"
  wordDetailCache: {},       // lemma_es → detail
  lemmaPosByEs: {},          // lemma_es → pos (for func-word detection in current sentence)
};

async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) throw new Error(`API ${path} feilet: ${r.status}`);
  return r.json();
}

function renderTpl(id) {
  const tpl = document.getElementById(id);
  return tpl.content.firstElementChild.cloneNode(true);
}
function clearApp() { app.innerHTML = ""; }
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- Login ----
async function showLogin() {
  clearApp();
  state.currentStudent = null;
  const page = renderTpl("tpl-login");
  app.appendChild(page);

  try { state.students = await api("/api/students"); } catch (e) { state.students = []; }

  const list = page.querySelector(".student-list");
  if (state.students.length === 0) {
    list.innerHTML = '<li class="empty">Ingen elever ennå. Legg til din første.</li>';
  } else {
    list.innerHTML = state.students.map(s => `
      <li data-sid="${s.id}">
        <div class="s-name">${escapeHtml(s.name)}</div>
        <div class="s-stats">${s.known_count} kjente · ${s.total_reviewed} repetert</div>
      </li>`).join("");
    list.querySelectorAll("li[data-sid]").forEach(li => {
      li.addEventListener("click", () => enterStudent(Number(li.dataset.sid)));
    });
  }

  page.querySelector('[data-action="show-new-student"]').addEventListener("click", () => {
    page.querySelector("[data-id=new-student-form]").classList.remove("hidden");
    page.querySelector("[data-id=new-student-form] input").focus();
  });
  page.querySelector('[data-action="cancel-new-student"]').addEventListener("click", () => {
    page.querySelector("[data-id=new-student-form]").classList.add("hidden");
  });
  page.querySelector("[data-id=new-student-form]").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = ev.target.name.value.trim();
    if (!name) return;
    try {
      const s = await api("/api/students", { method: "POST", body: { name } });
      state.students.push(s);
      await showLogin();
    } catch (e) { alert(e.message); }
  });
}

async function enterStudent(id) {
  state.currentStudent = state.students.find(x => x.id === id);
  state.sessionMode = state.currentStudent?.mode_preference || "self_grade";
  await showDashboard();
}

// ---- Dashboard ----
async function showDashboard() {
  clearApp();
  const page = renderTpl("tpl-dashboard");
  app.appendChild(page);

  page.querySelector(".student-name").textContent = state.currentStudent.name;
  page.querySelector('[data-action="back-to-login"]').addEventListener("click", showLogin);

  try {
    const data = await api(`/api/students/${state.currentStudent.id}/dashboard`);
    const stats = data.stats;
    state.sessionMode = data.student.mode_preference;

    page.querySelector("[data-id=known-count]").textContent = stats.known;
    page.querySelector("[data-id=introduced-count]").textContent = stats.introduced;
    page.querySelector("[data-id=due-count]").textContent = stats.due_now;
    page.querySelector("[data-id=box-1]").textContent = stats.leitner_boxes[1] || 0;
    page.querySelector("[data-id=box-2]").textContent = stats.leitner_boxes[2] || 0;
    page.querySelector("[data-id=box-3]").textContent = stats.leitner_boxes[3] || 0;
    page.querySelector("[data-id=learning-count]").textContent = stats.learning;
    page.querySelector("[data-id=learning-count-alt]").textContent = stats.learning;
    page.querySelector("[data-id=lapsed-count]").textContent = stats.lapsed;
    page.querySelector("[data-id=total-lemmas]").textContent = stats.total_lemmas;

    const dueBadge = page.querySelector("[data-id=due-badge]");
    if (stats.due_now > 0) {
      dueBadge.textContent = `${stats.due_now} ord til repetisjon`;
      dueBadge.classList.remove("empty");
    } else if (stats.introduced === 0) {
      dueBadge.textContent = "Du har ingen ord ennå — start for å lære nye";
      dueBadge.classList.add("empty");
    } else {
      dueBadge.textContent = "Ingen ord til repetisjon nå — øv på nye";
      dueBadge.classList.add("empty");
    }
  } catch (e) { console.error(e); }

  page.querySelectorAll(".toggle-btn").forEach(btn => {
    if (btn.dataset.mode === state.sessionMode) btn.classList.add("active");
    btn.addEventListener("click", async () => {
      page.querySelectorAll(".toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.sessionMode = btn.dataset.mode;
      try {
        await api(`/api/students/${state.currentStudent.id}/mode`, {
          method: "PUT", body: { mode_preference: btn.dataset.mode },
        });
      } catch (e) { console.error(e); }
    });
  });

  page.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tabTarget;
      page.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b === btn));
      page.querySelectorAll(".tab-content").forEach(c => c.classList.toggle("hidden", c.dataset.tab !== target));
    });
  });

  page.querySelector('[data-action="start-session"]').addEventListener("click", startSession);
}

// ---- Session ----
async function startSession() {
  try {
    const items = await api(`/api/students/${state.currentStudent.id}/session`);
    if (!items || items.length === 0) {
      alert("Ingen ord å repetere akkurat nå. Prøv igjen senere.");
      return;
    }
    state.sessionItems = items;
    state.sessionIdx = 0;
    state.sessionStats = { correct: 0, wrong: 0, new_intro: 0 };
    showReview();
  } catch (e) { alert(e.message); }
}

function showReview() {
  clearApp();
  const page = renderTpl("tpl-review");
  app.appendChild(page);

  page.querySelector('[data-action="exit-session"]').addEventListener("click", () => {
    if (confirm("Avslutte økten?")) showDashboard();
  });
  renderCurrentItem();
}

function renderCurrentItem() {
  const item = state.sessionItems[state.sessionIdx];
  if (!item) { showSessionEnd(); return; }

  // Reset per-item state
  state.showTranslation = false;
  state.expandedWord = null;
  state.markedMissed = new Set();
  state.lemmaPosByEs = {};
  state.itemStart = Date.now();

  const total = state.sessionItems.length;
  const current = state.sessionIdx + 1;
  app.querySelector(".progress-fill").style.width = `${(current / total) * 100}%`;
  app.querySelector("[data-id=progress-current]").textContent = current;
  app.querySelector("[data-id=progress-total]").textContent = total;

  const body = app.querySelector("[data-id=review-body]");
  body.innerHTML = "";

  if (item.kind === "intro_card") {
    renderIntroCard(body, item);
  } else if (state.sessionMode === "multiple_choice") {
    renderMultipleChoiceItem(body, item);
  } else {
    renderSelfGradeItem(body, item);
  }
}

async function renderIntroCard(body, item) {
  const wrap = document.createElement("div");
  wrap.className = "intro-wrap";
  wrap.innerHTML = `
    <div class="intro-label">Nytt ord</div>
    <h2 class="intro-heading">Bli kjent med et nytt spansk ord</h2>
    <div class="intro-lemma-card" data-id="intro-detail-slot"></div>
    <div class="action-row" style="max-width:400px; margin:0 auto;">
      <button class="action-btn primary" data-act="continue-intro">Jeg forstår — videre</button>
    </div>
  `;
  body.appendChild(wrap);

  // Load full detail for this lemma and render inline
  const slot = wrap.querySelector("[data-id=intro-detail-slot]");
  try {
    const detail = await api(`/api/lemmas/by-es/${encodeURIComponent(item.lemma_es)}?student_id=${state.currentStudent.id}`);
    state.wordDetailCache[item.lemma_es] = detail;
    slot.innerHTML = renderWordDetail(detail, item.lemma_es);
    // Hide "mark as missed" button on intro cards
    slot.querySelector("[data-act=toggle-missed]")?.remove();
  } catch (e) {
    slot.innerHTML = `<div class="word-detail">Kunne ikke hente ordinfo.</div>`;
  }

  wrap.querySelector('[data-act="continue-intro"]').addEventListener("click", () => {
    state.sessionIdx++;
    renderCurrentItem();
  });
}

// Self-grade: Alif-style. Sentence + 3 buttons. Word detail inline.
function renderSelfGradeItem(body, item) {
  // Pre-fetch word POS info so we can mark function words
  // (cheap: use word_mapping which already has lemma_es; we'll fetch detail lazily on click)
  const wrap = document.createElement("div");
  wrap.className = "sentence-card";

  if (item.is_new) {
    const tag = document.createElement("div");
    tag.className = "new-tag";
    tag.textContent = "Nytt ord";
    tag.style.textAlign = "center";
    wrap.appendChild(tag);
  }

  const sentence = document.createElement("div");
  sentence.className = "sentence-es";
  sentence.innerHTML = renderClickableSentence(item);
  wrap.appendChild(sentence);

  const wordDetailSlot = document.createElement("div");
  wordDetailSlot.dataset.id = "word-detail-slot";
  wrap.appendChild(wordDetailSlot);

  const answer = document.createElement("div");
  answer.className = "answer-section hidden-stable";
  answer.innerHTML = `<div class="sentence-no">${escapeHtml(item.sentence_no)}</div>`;
  wrap.appendChild(answer);

  const actions = document.createElement("div");
  actions.className = "action-row";
  actions.innerHTML = `
    <button class="action-btn no-idea" data-act="no_idea">Vet ikke</button>
    <button class="action-btn know-all" data-act="know_all">Kan alle</button>
    <button class="action-btn show-trans" data-act="show_trans">Vis oversettelse</button>
  `;
  wrap.appendChild(actions);

  body.appendChild(wrap);

  // Word click handlers
  wrap.querySelectorAll(".sentence-es .word:not(.func)").forEach(el => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const lemmaEs = el.dataset.lemmaEs;
      handleWordTap(lemmaEs, el);
    });
  });

  // Action buttons
  actions.querySelector('[data-act="no_idea"]').addEventListener("click", () => submitSentence(item, "no_idea"));
  actions.querySelector('[data-act="know_all"]').addEventListener("click", () => submitSentence(item, "know_all"));
  actions.querySelector('[data-act="show_trans"]').addEventListener("click", () => {
    state.showTranslation = true;
    answer.classList.remove("hidden-stable");
    actions.querySelector('[data-act="show_trans"]').remove();
  });
}

function renderClickableSentence(item) {
  const tokens = item.sentence_es.split(/(\s+)/);
  const sorted = [...(item.word_mapping || [])].sort((a, b) => a.position - b.position);
  let mapIdx = 0;

  return tokens.map(tok => {
    if (/^\s+$/.test(tok) || tok.length === 0) return tok;
    const leading = tok.match(/^[¿¡]+/)?.[0] || "";
    const trailing = tok.match(/[.,!?;:]+$/)?.[0] || "";
    const core = tok.slice(leading.length, tok.length - trailing.length);
    if (!core) return escapeHtml(tok);

    const mapping = sorted[mapIdx++];
    const lemmaEs = mapping?.lemma_es || "";
    if (!lemmaEs) return escapeHtml(tok);

    const isTarget = lemmaEs === item.lemma_es;
    // We don't know POS for arbitrary lemmas without a fetch. We'll mark target/non-func based on cache; default = clickable.
    const cached = state.wordDetailCache[lemmaEs];
    const isFunc = cached ? FUNC_POS.has(cached.pos) : false;
    const cls = ["word"];
    if (isTarget) cls.push("target");
    if (isFunc) cls.push("func");
    const attr = `class="${cls.join(" ")}" data-lemma-es="${escapeHtml(lemmaEs)}"`;
    return `${escapeHtml(leading)}<span ${attr}>${escapeHtml(core)}</span>${escapeHtml(trailing)}`;
  }).join("");
}

async function handleWordTap(lemmaEs, el) {
  // Toggle expanded if same word; else load + expand.
  if (state.expandedWord === lemmaEs) {
    state.expandedWord = null;
    el.classList.remove("expanded");
    document.querySelector("[data-id=word-detail-slot]").innerHTML = "";
    return;
  }

  // Mark previous as not expanded
  document.querySelectorAll(".sentence-es .word.expanded").forEach(w => w.classList.remove("expanded"));
  el.classList.add("expanded");
  state.expandedWord = lemmaEs;

  // Load + render
  const slot = document.querySelector("[data-id=word-detail-slot]");
  slot.innerHTML = `<div class="word-detail" style="opacity:0.6">Laster…</div>`;

  let detail = state.wordDetailCache[lemmaEs];
  if (!detail) {
    try {
      detail = await api(`/api/lemmas/by-es/${encodeURIComponent(lemmaEs)}?student_id=${state.currentStudent.id}`);
      state.wordDetailCache[lemmaEs] = detail;
    } catch (e) {
      slot.innerHTML = `<div class="word-detail">Kunne ikke hente ordinfo.</div>`;
      return;
    }
  }
  slot.innerHTML = renderWordDetail(detail, lemmaEs);

  // Wire "mark as missed" toggle
  slot.querySelector("[data-act=toggle-missed]")?.addEventListener("click", () => {
    if (state.markedMissed.has(lemmaEs)) {
      state.markedMissed.delete(lemmaEs);
    } else {
      state.markedMissed.add(lemmaEs);
    }
    updateMarkedClasses();
    refreshActionButtons();
  });
}

function updateMarkedClasses() {
  document.querySelectorAll(".sentence-es .word").forEach(w => {
    if (state.markedMissed.has(w.dataset.lemmaEs)) {
      w.classList.add("marked-missed");
    } else {
      w.classList.remove("marked-missed");
    }
  });
  // Update the "mark missed" button text in the open word detail
  const btn = document.querySelector("[data-act=toggle-missed]");
  if (btn) {
    const isMarked = state.markedMissed.has(state.expandedWord);
    btn.textContent = isMarked ? "Fjern markering" : "Marker som ukjent";
    btn.classList.toggle("marked", isMarked);
  }
}

function refreshActionButtons() {
  // If any words are marked → "Kan alle" becomes "Fortsett"
  const knowBtn = document.querySelector('[data-act="know_all"]');
  if (!knowBtn) return;
  if (state.markedMissed.size > 0) {
    knowBtn.textContent = "Fortsett";
    knowBtn.classList.remove("know-all");
    knowBtn.classList.add("continue");
    knowBtn.dataset.act = "continue";
    knowBtn.removeEventListener("click", knowBtn._handler);
    knowBtn._handler = () => submitSentence(state.sessionItems[state.sessionIdx], "continue");
    knowBtn.addEventListener("click", knowBtn._handler);
  }
}

function renderWordDetail(d, lemmaEs) {
  const isMarked = state.markedMissed.has(lemmaEs);
  const genderBadge = (d.gender && d.gender !== "none")
    ? `<span class="wd-badge gender-${d.gender}">${d.gender === "m" ? "maskulin" : d.gender === "f" ? "feminin" : d.gender}</span>` : "";
  const quirk = d.article_quirk
    ? `<div class="wd-section"><div class="wd-quirk">${escapeHtml(d.article_quirk)}</div></div>` : "";

  let conj = "";
  if (d.conjugation_applicable && d.conjugation_present) {
    const c = d.conjugation_present;
    conj = `<div class="wd-section">
      <div class="wd-section-title">Presens</div>
      <table class="wd-conj">
        <tr><td>yo</td><td>${escapeHtml(c.yo)}</td><td>nosotros</td><td>${escapeHtml(c.nosotros)}</td></tr>
        <tr><td>tú</td><td>${escapeHtml(c.tu)}</td><td>vosotros</td><td>${escapeHtml(c.vosotros)}</td></tr>
        <tr><td>él/ella</td><td>${escapeHtml(c.el)}</td><td>ellos/ellas</td><td>${escapeHtml(c.ellos)}</td></tr>
      </table>
    </div>`;
  }
  let agr = "";
  if (d.agreement_forms && Object.values(d.agreement_forms).some(v => v)) {
    const a = d.agreement_forms;
    agr = `<div class="wd-section">
      <div class="wd-section-title">Kongruens</div>
      <table class="wd-conj">
        <tr><td>mask.sg</td><td>${escapeHtml(a.masc_sg || "")}</td><td>mask.pl</td><td>${escapeHtml(a.masc_pl || "")}</td></tr>
        <tr><td>fem.sg</td><td>${escapeHtml(a.fem_sg || "")}</td><td>fem.pl</td><td>${escapeHtml(a.fem_pl || "")}</td></tr>
      </table>
    </div>`;
  }
  const plural = d.plural_form
    ? `<div class="wd-section"><span class="wd-section-title">Flertall: </span><strong>${escapeHtml(d.plural_form)}</strong></div>` : "";
  const hook = d.memory_hook_no
    ? `<div class="wd-section"><div class="wd-section-title">Huskeregel</div><div>${escapeHtml(d.memory_hook_no)}</div></div>` : "";
  const etym = d.etymology_no
    ? `<div class="wd-section"><div class="wd-section-title">Etymologi</div><div>${escapeHtml(d.etymology_no)}</div></div>` : "";
  const example = d.example_es
    ? `<div class="wd-section">
         <div class="wd-section-title">Eksempel</div>
         <div class="wd-example-es">${escapeHtml(d.example_es)}</div>
         <div class="wd-example-no">${escapeHtml(d.example_no)}</div>
       </div>` : "";

  let status = "";
  if (d.card_state) {
    let label = "";
    if (d.card_state === "acquiring") label = `Lærer (boks ${d.leitner_box})`;
    else if (d.card_state === "learning") label = "Under langtidslæring";
    else if (d.card_state === "known") label = "Kjent";
    else if (d.card_state === "lapsed") label = "Glemt — må repeteres";
    else label = d.card_state;
    status = `<div class="wd-status">
      <div class="wd-status-title">Din status</div>
      <div>${label} · sett ${d.times_seen} ganger (${d.times_correct} riktig)</div>
    </div>`;
  }

  // Func words (article/preposition/conjunction) don't get a "mark missed" — they're not graded
  const isFunc = FUNC_POS.has(d.pos);
  const markBtn = isFunc ? "" :
    `<button class="btn btn-secondary" style="width:100%; margin-top:0.75rem;" data-act="toggle-missed">${isMarked ? "Fjern markering" : "Marker som ukjent"}</button>`;

  return `<div class="word-detail">
    <div class="wd-head">
      <div class="wd-es">${escapeHtml(d.lemma_es)}</div>
      <div class="wd-gloss">${escapeHtml(d.gloss_no)}</div>
      <div class="wd-meta">
        <span class="wd-badge">${escapeHtml(d.pos)}</span>
        ${genderBadge}
        <span class="wd-badge cefr">${escapeHtml(d.cefr_level)}</span>
        <span style="color:var(--text-dim);">rang ~${d.frequency_rank}</span>
      </div>
    </div>
    ${plural}
    ${quirk}
    ${conj}
    ${agr}
    ${hook}
    ${etym}
    ${example}
    ${status}
    ${markBtn}
  </div>`;
}

async function submitSentence(item, action) {
  const ms = Date.now() - state.itemStart;
  // Compute per-lemma ratings:
  //  no_idea → ALL non-func words rated 1
  //  know_all → ALL non-func words rated 3
  //  continue → marked words rated 1, others rated 3
  const lemmaRatings = computeLemmaRatings(item, action);

  try {
    await api(`/api/students/${state.currentStudent.id}/review`, {
      method: "POST",
      body: {
        sentence_id: item.sentence_id,
        action,                         // string label for log
        mode: state.sessionMode,
        response_ms: ms,
        lemma_ratings: lemmaRatings,    // [{lemma_es, rating}]
      },
    });
    if (action === "know_all") state.sessionStats.correct++;
    else if (action === "no_idea") state.sessionStats.wrong++;
    else state.sessionStats.correct++;  // continue counted as partial-correct
    if (item.is_new) state.sessionStats.new_intro++;
  } catch (e) { console.error(e); }
  state.sessionIdx++;
  renderCurrentItem();
}

function computeLemmaRatings(item, action) {
  const seen = new Set();
  const ratings = [];
  for (const w of item.word_mapping || []) {
    if (!w.lemma_es || seen.has(w.lemma_es)) continue;
    seen.add(w.lemma_es);
    const cached = state.wordDetailCache[w.lemma_es];
    const isFunc = cached ? FUNC_POS.has(cached.pos) : false;
    if (isFunc) continue;  // skip articles, prepositions, conjunctions
    let rating;
    if (action === "no_idea") rating = 1;
    else if (action === "know_all") rating = 3;
    else rating = state.markedMissed.has(w.lemma_es) ? 1 : 3;
    ratings.push({ lemma_es: w.lemma_es, rating });
  }
  return ratings;
}

// Multiple choice mode (kept simple — sentence + 4 options + feedback)
function renderMultipleChoiceItem(body, item) {
  const wrap = document.createElement("div");
  wrap.className = "sentence-card";

  if (item.is_new) {
    const tag = document.createElement("div");
    tag.className = "new-tag";
    tag.textContent = "Nytt ord";
    tag.style.textAlign = "center";
    wrap.appendChild(tag);
  }

  const sentence = document.createElement("div");
  sentence.className = "sentence-es";
  sentence.innerHTML = renderClickableSentence(item);
  wrap.appendChild(sentence);

  const slot = document.createElement("div");
  slot.dataset.id = "word-detail-slot";
  wrap.appendChild(slot);

  const opts = [{ text: item.sentence_no, correct: true },
                ...item.distractors_no.map(d => ({ text: d, correct: false }))];
  shuffle(opts);

  const optsWrap = document.createElement("div");
  optsWrap.className = "mc-options";
  opts.forEach(o => {
    const btn = document.createElement("button");
    btn.className = "mc-option";
    btn.textContent = o.text;
    btn.addEventListener("click", () => {
      Array.from(optsWrap.children).forEach(el => el.disabled = true);
      btn.classList.add(o.correct ? "correct" : "incorrect");
      if (!o.correct) {
        Array.from(optsWrap.children).forEach((el, i) => {
          if (opts[i].correct) el.classList.add("correct");
        });
      }
      const fb = document.createElement("div");
      fb.className = `mc-feedback ${o.correct ? "good" : "bad"}`;
      fb.textContent = o.correct ? "Riktig" : "Feil";
      wrap.appendChild(fb);

      const action = o.correct ? "know_all" : "no_idea";
      // Auto-advance after short delay so user can see feedback
      setTimeout(() => submitSentence(item, action), 1100);
    });
    optsWrap.appendChild(btn);
  });
  wrap.appendChild(optsWrap);

  body.appendChild(wrap);

  // Word taps still allowed (just for inspection — no marking in MC mode)
  wrap.querySelectorAll(".sentence-es .word:not(.func)").forEach(el => {
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      handleWordTap(el.dataset.lemmaEs, el);
    });
  });
}

function showSessionEnd() {
  clearApp();
  const page = renderTpl("tpl-session-end");
  app.appendChild(page);
  const stats = state.sessionStats;
  const total = stats.correct + stats.wrong;
  page.querySelector("[data-id=end-stats]").innerHTML = `
    <div class="state-row"><span>Setninger</span><strong>${total}</strong></div>
    <div class="state-row"><span>Kunne</span><strong>${stats.correct}</strong></div>
    <div class="state-row"><span>Vet ikke</span><strong>${stats.wrong}</strong></div>
    <div class="state-row"><span>Nye ord</span><strong>${stats.new_intro}</strong></div>
  `;
  page.querySelector('[data-action="back-to-dashboard"]').addEventListener("click", showDashboard);
}

function shuffle(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

showLogin();
