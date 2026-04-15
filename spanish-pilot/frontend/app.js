// Spansk — norsk pilot. Vanilla JS, no framework.
// State is stored in a single object; views render to #app.

const API = ""; // same origin
const app = document.getElementById("app");

// ---- Global state ----
const state = {
  students: [],
  currentStudent: null,
  sessionItems: [],
  sessionIdx: 0,
  sessionMode: "self_grade",
  sessionStats: { correct: 0, wrong: 0, new_intro: 0 },
  itemStart: 0,
  showTranslation: false,
  modalOpen: false,
};

// ---- Utilities ----
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

// ---- Login view ----
async function showLogin() {
  clearApp();
  state.currentStudent = null;
  const page = renderTpl("tpl-login");
  app.appendChild(page);

  try {
    state.students = await api("/api/students");
  } catch (e) { state.students = []; }

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
    } catch (e) {
      alert(e.message);
    }
  });
}

async function enterStudent(id) {
  const s = state.students.find(x => x.id === id);
  state.currentStudent = s;
  state.sessionMode = s?.mode_preference || "self_grade";
  await showDashboard();
}

// ---- Dashboard view ----
async function showDashboard() {
  clearApp();
  const page = renderTpl("tpl-dashboard");
  app.appendChild(page);

  page.querySelector(".student-name").textContent = state.currentStudent.name;
  page.querySelector('[data-action="back-to-login"]').addEventListener("click", showLogin);

  let stats;
  try {
    const data = await api(`/api/students/${state.currentStudent.id}/dashboard`);
    stats = data.stats;
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

    // Due badge on start tab
    const dueBadge = page.querySelector("[data-id=due-badge]");
    if (stats.due_now > 0) {
      dueBadge.textContent = `${stats.due_now} ord til repetisjon`;
      dueBadge.classList.remove("empty");
    } else if (stats.introduced === 0) {
      dueBadge.textContent = "Du har ingen ord ennå — start for å lære nye";
      dueBadge.classList.add("empty");
    } else {
      dueBadge.textContent = "Ingen ord til repetisjon nå — øv på nye ord";
      dueBadge.classList.add("empty");
    }
  } catch (e) {
    console.error(e);
  }

  // Mode toggle
  page.querySelectorAll(".toggle-btn").forEach(btn => {
    if (btn.dataset.mode === state.sessionMode) btn.classList.add("active");
    btn.addEventListener("click", async () => {
      page.querySelectorAll(".toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.sessionMode = btn.dataset.mode;
      try {
        await api(`/api/students/${state.currentStudent.id}/mode`, {
          method: "PUT",
          body: { mode_preference: btn.dataset.mode },
        });
      } catch (e) { console.error(e); }
    });
  });

  // Bottom tab nav
  page.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tabTarget;
      page.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b === btn));
      page.querySelectorAll(".tab-content").forEach(c => c.classList.toggle("hidden", c.dataset.tab !== target));
    });
  });

  page.querySelector('[data-action="start-session"]').addEventListener("click", startSession);
}

// ---- Review session ----
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
  if (!item) {
    showSessionEnd();
    return;
  }
  const total = state.sessionItems.length;
  const current = state.sessionIdx + 1;
  const progressFill = app.querySelector(".progress-fill");
  progressFill.style.width = `${(current / total) * 100}%`;
  app.querySelector("[data-id=progress-current]").textContent = current;
  app.querySelector("[data-id=progress-total]").textContent = total;

  state.showTranslation = false;
  state.itemStart = Date.now();

  const body = app.querySelector("[data-id=review-body]");
  body.innerHTML = "";

  const newBadge = item.is_new ? '<div class="new-badge">NYTT ORD</div>' : '';

  // Build Spanish sentence with clickable words
  const esHtml = buildClickableSentence(item.sentence_es, item.word_mapping, item.lemma_es);

  const card = document.createElement("div");
  card.className = "card-wrap";
  card.innerHTML = `
    ${newBadge}
    <div class="card-es">${esHtml}</div>
    <div class="card-no hidden" data-id="translation">${escapeHtml(item.sentence_no)}</div>
  `;
  body.appendChild(card);

  // Attach word-click handlers (by lemma_es)
  card.querySelectorAll(".clickable-word[data-lemma-es]").forEach(el => {
    el.addEventListener("click", () => showWordModal(el.dataset.lemmaEs));
  });

  if (state.sessionMode === "self_grade") {
    renderSelfGrade(body, item);
  } else {
    renderMultipleChoice(body, item);
  }
}

function buildClickableSentence(es, wordMapping, targetLemmaEs) {
  // word_mapping: [{position, form, lemma_es, grammatical_note}]
  // We walk whitespace-separated tokens and match to mapping entries in order.
  const tokens = es.split(/(\s+)/);
  const sorted = [...(wordMapping || [])].sort((a, b) => a.position - b.position);
  let mapIdx = 0;

  return tokens.map(tok => {
    if (/^\s+$/.test(tok) || tok.length === 0) return tok;
    const leading = tok.match(/^[¿¡]+/)?.[0] || "";
    const trailing = tok.match(/[.,!?;:]+$/)?.[0] || "";
    const core = tok.slice(leading.length, tok.length - trailing.length);
    if (!core) return escapeHtml(tok);

    const mapping = sorted[mapIdx++];
    const lemmaEs = mapping?.lemma_es || "";
    const isTarget = lemmaEs && lemmaEs === targetLemmaEs;
    const attrs = lemmaEs
      ? `class="clickable-word${isTarget ? ' word-highlight' : ''}" data-lemma-es="${escapeAttr(lemmaEs)}"`
      : '';
    return `${escapeHtml(leading)}<span ${attrs}>${escapeHtml(core)}</span>${escapeHtml(trailing)}`;
  }).join("");
}

function renderSelfGrade(body, item) {
  const btn = document.createElement("button");
  btn.className = "show-answer-btn";
  btn.textContent = "Vis oversettelse";
  btn.addEventListener("click", () => {
    state.showTranslation = true;
    body.querySelector("[data-id=translation]").classList.remove("hidden");
    btn.remove();

    const ratings = document.createElement("div");
    ratings.className = "ratings";
    ratings.innerHTML = `
      <button class="rating-btn" data-rating="1">Igjen<small>< 1 min</small></button>
      <button class="rating-btn" data-rating="2">Vanskelig<small>snart</small></button>
      <button class="rating-btn" data-rating="3">Greit<small>planlagt</small></button>
      <button class="rating-btn" data-rating="4">Lett<small>senere</small></button>
    `;
    ratings.querySelectorAll(".rating-btn").forEach(b => {
      b.addEventListener("click", () => submitRating(Number(b.dataset.rating), null));
    });
    body.appendChild(ratings);
  });
  body.appendChild(btn);
}

function renderMultipleChoice(body, item) {
  // Build options: correct + 3 distractors, shuffled
  const opts = [{ text: item.sentence_no, correct: true }];
  for (const d of item.distractors_no) opts.push({ text: d, correct: false });
  shuffle(opts);

  const wrap = document.createElement("div");
  wrap.className = "mc-options";
  opts.forEach(o => {
    const btn = document.createElement("button");
    btn.className = "mc-option";
    btn.textContent = o.text;
    btn.addEventListener("click", () => {
      // Reveal correct/incorrect, offer continue
      Array.from(wrap.children).forEach(el => el.disabled = true);
      const correct = o.correct;
      btn.classList.add(correct ? "correct" : "incorrect");
      if (!correct) {
        Array.from(wrap.children).forEach(el => {
          if (opts[Array.from(wrap.children).indexOf(el)].correct) {
            el.classList.add("correct");
          }
        });
      }
      // Also reveal official translation below
      body.querySelector("[data-id=translation]").classList.remove("hidden");

      // Feedback + continue
      const fb = document.createElement("div");
      fb.className = `mc-feedback ${correct ? "good" : "bad"}`;
      fb.textContent = correct ? "Riktig!" : "Feil.";
      body.appendChild(fb);

      const cont = document.createElement("button");
      cont.className = "btn btn-primary mc-continue-btn";
      cont.textContent = "Fortsett";
      cont.addEventListener("click", () => {
        // Map correct/incorrect to rating: correct→3 Good, incorrect→1 Again
        const rating = correct ? 3 : 1;
        submitRating(rating, correct);
      });
      body.appendChild(cont);
    });
    wrap.appendChild(btn);
  });
  body.appendChild(wrap);
}

async function submitRating(rating, correct) {
  const item = state.sessionItems[state.sessionIdx];
  const ms = Date.now() - state.itemStart;
  try {
    await api(`/api/students/${state.currentStudent.id}/review`, {
      method: "POST",
      body: {
        card_id: item.card_id,
        sentence_id: item.sentence_id,
        rating,
        mode: state.sessionMode,
        correct,
        response_ms: ms,
      },
    });
    if (rating >= 3) state.sessionStats.correct++; else state.sessionStats.wrong++;
    if (item.is_new) state.sessionStats.new_intro++;
  } catch (e) { console.error(e); }
  state.sessionIdx++;
  renderCurrentItem();
}

function showSessionEnd() {
  clearApp();
  const page = renderTpl("tpl-session-end");
  app.appendChild(page);

  const stats = state.sessionStats;
  const total = stats.correct + stats.wrong;
  page.querySelector("[data-id=end-stats]").innerHTML = `
    <div class="state-row"><span>Repetert</span><strong>${total}</strong></div>
    <div class="state-row"><span>Riktig</span><strong>${stats.correct}</strong></div>
    <div class="state-row"><span>Feil</span><strong>${stats.wrong}</strong></div>
    <div class="state-row"><span>Nye ord introdusert</span><strong>${stats.new_intro}</strong></div>
  `;

  page.querySelector('[data-action="back-to-dashboard"]').addEventListener("click", showDashboard);
}

// ---- Word detail modal ----
async function showWordModal(lemmaEs) {
  try {
    const url = `/api/lemmas/by-es/${encodeURIComponent(lemmaEs)}?student_id=${state.currentStudent.id}`;
    const detail = await api(url);
    openModalWithDetail(detail);
  } catch (e) {
    console.error("Kunne ikke hente ordinfo:", e);
  }
}

function openModalWithDetail(d) {
  const modal = renderTpl("tpl-word-modal");
  document.body.appendChild(modal);
  state.modalOpen = true;

  const wd = modal.querySelector("[data-id=word-detail]");
  wd.innerHTML = buildWordDetailHtml(d);

  modal.addEventListener("click", (ev) => {
    const action = ev.target.closest("[data-action=close-modal]");
    if (action || ev.target === modal) closeModal();
  });
}

function closeModal() {
  const m = document.querySelector(".modal-backdrop");
  if (m) m.remove();
  state.modalOpen = false;
}

function buildWordDetailHtml(d) {
  const genderBadge = d.gender && d.gender !== "none"
    ? `<span class="badge-gender badge-${d.gender}">${d.gender === "m" ? "maskulin" : d.gender === "f" ? "feminin" : d.gender}</span>`
    : "";
  const quirk = d.article_quirk
    ? `<div class="w-section"><div class="w-quirk">⚠ ${escapeHtml(d.article_quirk)}</div></div>` : "";
  let conj = "";
  if (d.conjugation_applicable && d.conjugation_present) {
    const c = d.conjugation_present;
    conj = `<div class="w-section">
      <div class="w-section-title">Presens</div>
      <table class="w-conj">
        <tr><td>yo</td><td>${escapeHtml(c.yo)}</td><td>nosotros</td><td>${escapeHtml(c.nosotros)}</td></tr>
        <tr><td>tú</td><td>${escapeHtml(c.tu)}</td><td>vosotros</td><td>${escapeHtml(c.vosotros)}</td></tr>
        <tr><td>él/ella</td><td>${escapeHtml(c.el)}</td><td>ellos/ellas</td><td>${escapeHtml(c.ellos)}</td></tr>
      </table>
    </div>`;
  }
  let agr = "";
  if (d.agreement_forms && Object.values(d.agreement_forms).some(v => v)) {
    const a = d.agreement_forms;
    agr = `<div class="w-section">
      <div class="w-section-title">Kongruens</div>
      <table class="w-conj">
        <tr><td>mask.sg</td><td>${escapeHtml(a.masc_sg || "")}</td><td>mask.pl</td><td>${escapeHtml(a.masc_pl || "")}</td></tr>
        <tr><td>fem.sg</td><td>${escapeHtml(a.fem_sg || "")}</td><td>fem.pl</td><td>${escapeHtml(a.fem_pl || "")}</td></tr>
      </table>
    </div>`;
  }
  const plural = d.plural_form
    ? `<div class="w-section"><span class="w-section-title">Flertall: </span><strong>${escapeHtml(d.plural_form)}</strong></div>` : "";
  const hook = d.memory_hook_no
    ? `<div class="w-section"><div class="w-section-title">Huskeregel</div><div>${escapeHtml(d.memory_hook_no)}</div></div>` : "";
  const etym = d.etymology_no
    ? `<div class="w-section"><div class="w-section-title">Etymologi</div><div>${escapeHtml(d.etymology_no)}</div></div>` : "";
  const example = d.example_es
    ? `<div class="w-section"><div class="w-section-title">Eksempel</div>
         <div style="color:var(--accent-dark); font-size:1rem">${escapeHtml(d.example_es)}</div>
         <div style="color:var(--text-muted); font-style:italic; font-size:0.95rem">${escapeHtml(d.example_no)}</div>
       </div>` : "";
  let status = "";
  if (d.card_state) {
    let label = "";
    if (d.card_state === "acquiring") label = `Lærer (boks ${d.leitner_box})`;
    else if (d.card_state === "learning") label = "Under langtidslæring";
    else if (d.card_state === "known") label = "Kjent";
    else if (d.card_state === "lapsed") label = "Glemt — må repeteres";
    else label = d.card_state;
    status = `<div class="w-status">
      <div class="w-status-title">Din status</div>
      <div>${label} · sett ${d.times_seen} ganger (${d.times_correct} riktig)</div>
    </div>`;
  }

  return `
    <div class="w-head">
      <div class="w-es">${escapeHtml(d.lemma_es)}</div>
      <div class="w-gloss">${escapeHtml(d.gloss_no)}</div>
      <div class="w-meta">
        <span class="badge-pos">${escapeHtml(d.pos)}</span>
        ${genderBadge}
        <span class="badge-cefr">${escapeHtml(d.cefr_level)}</span>
        <span>rang ~${d.frequency_rank}</span>
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
  `;
}

// ---- Utilities ----
function shuffle(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}
function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

// Keyboard: Esc closes modal
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.modalOpen) closeModal();
});

// ---- Boot ----
showLogin();
