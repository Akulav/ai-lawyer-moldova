"use strict";
const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = n => (n ?? 0).toLocaleString("ro-RO");
const fmtDate = d => { if (!d) return "—"; const [y, m, z] = d.split("-"); return `${z}.${m}.${y}`; };
const api = async (url, opt) => {
  const r = await fetch(url, opt);
  if (!r.ok) { let m = `Eroare ${r.status}`; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.json();
};
const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(t._t); t._t = setTimeout(() => t.hidden = true, 4000); }

const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
const savedTheme = store.get("theme"); if (savedTheme) document.documentElement.dataset.theme = savedTheme;
$("#themeBtn").onclick = () => {
  const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "light" : "dark"; store.set("theme", dark ? "light" : "dark");
};

const DISCLAIMER = `
  <h2>Înainte de a începe</h2>
  <p>Acest program oferă <b>informații juridice generale</b>, generate automat de un model AI. <b>Nu este avocat</b> și nu oferă consultanță juridică.</p>
  <ul>
    <li><b>Poate greși.</b> Modelele AI pot interpreta greșit sau omite prevederi. Verificați întotdeauna sursa citată.</li>
    <li><b>Poate fi incomplet sau depășit.</b> Indexul provine în principal din textele publicate în Monitorul Oficial. Starea unui act („abrogat”, „modificat”) este <b>detectată automat</b> și poate fi greșită. Lipsa unei abrogări detectate nu garantează că actul este în vigoare.</li>
    <li><b>Nu creează o relație avocat-client.</b> Nimeni nu răspunde juridic pentru deciziile luate pe baza acestor informații.</li>
    <li><b>Pentru decizii importante</b> (instanță, termene, sume mari, libertate, imigrare) consultați un avocat autorizat sau autoritatea competentă.</li>
    <li><b>Confidențialitate.</b> Modelul și întrebările rămân pe calculatorul dvs. Internetul e folosit doar pentru a descărca legislația și modelele.</li>
  </ul>
  <p>Textul oficial rămâne cel publicat în <a href="https://monitorul.gov.md" target="_blank" rel="noopener">Monitorul Oficial</a> și în <a href="https://www.legis.md" target="_blank" rel="noopener">Registrul de stat al actelor juridice</a>.</p>`;
function showDisclaimer(first) {
  const m = $("#modal");
  m.innerHTML = `<div class="dialog" role="dialog" aria-modal="true">${DISCLAIMER}<div class="dialog-foot"><button class="btn primary" id="okD">${first ? "Am înțeles și accept" : "Închide"}</button></div></div>`;
  m.hidden = false;
  $("#okD").onclick = async () => { m.hidden = true; if (first) await post("/api/disclaimer"); };
}
$("#aboutBtn").onclick = () => showDisclaimer(false);

/* ---- status ---- */
let STATUS = null;
const LLM_TEXT = { oprit: "Motor AI oprit", nepornit: "Pornesc", descarc_runtime: "Descarc motorul AI", pornesc: "Pornesc motorul AI", eroare: "Eroare motor AI" };
async function pollStatus() {
  try {
    STATUS = await api("/api/status");
    const { llm, index } = STATUS;
    const pct = index.issues_total ? Math.floor(100 * index.issues_done / index.issues_total) : 0;
    $("#navIndex").textContent = index.issues_total && pct < 100 ? pct + "%" : "";
    const bad = (index.issues.error || 0) + (index.issues.no_text || 0) + (index.retrying || 0);
    $("#navProblems").textContent = bad ? fmtNum(bad) : "";
    let s;
    if (llm.ready) s = `<span class="dot ok"></span>${esc(llm.active)}`;
    else if (llm.state === "eroare") s = `<span class="dot err"></span>Eroare motor AI`;
    else if (llm.state === "oprit") s = `<span class="dot"></span>Motor AI oprit`;
    else if (llm.pull) s = `<span class="dot busy"></span>Descarc modelul ${Math.floor(100 * (llm.pull.progress || 0))}%`;
    else s = `<span class="dot busy"></span>${LLM_TEXT[llm.state] || "Pregătesc"}`;
    $("#railStatus").innerHTML = $("#topStatus").innerHTML = s;
    renderSetup();
    if (route().name === "index") renderIndexLive();
  } catch { $("#railStatus").innerHTML = $("#topStatus").innerHTML = `<span class="dot err"></span>Serverul nu răspunde`; }
}
setInterval(pollStatus, 2500);

function setupHtml() {
  if (!STATUS) return "";
  const { llm } = STATUS;
  if (llm.ready) return "";
  if (llm.state === "oprit") return `<div class="setup"><b>Motorul AI este oprit</b>Porniți-l din <a href="#models">Modele AI</a> pentru a pune întrebări. Căutarea și indexarea funcționează în continuare.</div>`;
  if (llm.state === "eroare") return `<div class="setup"><b>Motorul AI nu a pornit</b>${esc(llm.error)}<br>Puteți folosi în continuare Căutarea.</div>`;
  let title = "Pregătesc asistentul, o singură dată", detail = "", p = null;
  if (llm.state === "descarc_runtime") { detail = "Descarc motorul AI (circa 1,6 GB)"; p = llm.runtime_progress; }
  else if (llm.pull && llm.pull.state === "eroare") { title = "Modelul nu s-a descărcat"; detail = llm.pull.error + " — reîncercați din secțiunea Modele AI."; }
  else if (llm.pull) { detail = `Descarc modelul ${llm.active}`; p = llm.pull.progress; }
  else detail = "Pornesc motorul AI…";
  return `<div class="setup"><b>${esc(title)}</b>${esc(detail)}${p != null ? ` · ${Math.floor(p * 100)}%<div class="bar"><i style="width:${p * 100}%"></i></div>` : ""}<br><small>Căutarea funcționează deja. Puteți închide fereastra browserului, descărcarea continuă.</small></div>`;
}
function renderSetup() { const el = $("#setupSlot"); if (el) el.innerHTML = setupHtml(); const b = $("#sendBtn"); if (b && !busy) b.disabled = !(STATUS && STATUS.llm.ready); }

/* ---- router ---- */
function route() { const [name, arg] = location.hash.slice(1).split("/"); return { name: name || "chat", arg }; }
const VIEWS = { chat: viewChat, search: viewSearch, tree: viewTree, act: viewAct, index: viewIndex, problems: viewProblems, models: viewModels };
/* ---- phone menu: the rail slides in over the page ---- */
function setMenu(open) {
  $("#rail").classList.toggle("open", open);
  $("#scrim").hidden = !open;
  $("#menuBtn").setAttribute("aria-expanded", String(open));
}
$("#menuBtn").onclick = () => setMenu(!$("#rail").classList.contains("open"));
$("#scrim").onclick = () => setMenu(false);
addEventListener("keydown", e => { if (e.key === "Escape") setMenu(false); });
// Choosing a page or a conversation closes it; the delete button inside a conversation row does not.
$("#rail").addEventListener("click", e => { if (e.target.closest("a")) setMenu(false); });

function go() {
  setMenu(false);
  const { name, arg } = route();
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("on", a.dataset.nav === name || (name === "act" && a.dataset.nav === "search")));
  clearInterval(modelsTimer);
  (VIEWS[name] || viewChat)(arg ? decodeURIComponent(arg) : undefined);
}
addEventListener("hashchange", go);

/* ---- markdown (input is always escaped first) ---- */
function inline(s) {
  return s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<em>$2</em>")
    .replace(/\[S(\d+)\]/g, '<button class="cite" data-s="$1" title="Vezi sursa">S$1</button>');
}
function md(src) {
  const lines = esc(src).split("\n"); let out = "", list = null;
  const close = () => { if (list) { out += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const l = raw.trim(); let m;
    if (!l) { close(); continue; }
    if ((m = l.match(/^#{1,2}\s+(.*)/))) { close(); out += `<h2>${inline(m[1])}</h2>`; }
    else if ((m = l.match(/^#{3,6}\s+(.*)/))) { close(); out += `<h3>${inline(m[1])}</h3>`; }
    else if ((m = l.match(/^[-*•]\s+(.*)/))) { if (list !== "ul") { close(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^\d+[.)]\s+(.*)/))) { if (list !== "ol") { close(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; }
    else { close(); out += `<p>${inline(l)}</p>`; }
  }
  close(); return out;
}
function statusBadge(a) {
  const cls = a.status === "abrogat" ? "b-ab" : a.status === "abrogare_viitoare" ? "b-fut" : "b-in";
  const lbl = a.status === "abrogat" ? "Abrogat" : a.status === "abrogare_viitoare" ? "Va fi abrogat" : "Fără abrogare detectată";
  return `<span class="badge ${cls}" title="${esc(a.status_note || "Stare detectată automat din Monitorul Oficial")}">${lbl}</span>`;
}
const KIND = { lege: "Lege", cod: "Cod", hotarare: "Hotărâre", decret: "Decret", ordin: "Ordin", regulament: "Regulament", dispozitie: "Dispoziție", decizie: "Decizie", tratat: "Tratat", constitutie: "Constituție", alt: "Alt act" };

/* ---- chat ---- */
let conversationId = null, busy = false;
const EXAMPLES = [
  ["Vamă", "Trebuie să declar la întoarcerea în țară un iPhone nedespachetat, dacă este primit cadou?"],
  ["Muncă", "În cât timp trebuie să-mi plătească angajatorul salariul după demisie?"],
  ["Locuință", "Proprietarul poate mări chiria în timpul contractului?"],
  ["Rutier", "Ce se întâmplă dacă nu plătesc amenda pentru viteză la timp?"],
];
function viewChat(arg) {
  if (arg) conversationId = +arg; else conversationId = null;
  main.innerHTML = `<div class="chat"><div class="chat-scroll" id="scroll"><div class="thread" id="thread"></div></div>
    <div class="composer-wrap"><form class="composer" id="composer">
      <textarea id="q" rows="1" placeholder="Descrieți situația dvs., în cuvinte simple…" aria-label="Întrebare"></textarea>
      <button class="send" id="sendBtn" disabled>Întreabă</button></form>
      <div class="fine">Informații generale, nu consultanță juridică. Verificați sursele citate.</div></div></div>`;
  const q = $("#q");
  q.oninput = () => { q.style.height = "auto"; q.style.height = q.scrollHeight + "px"; };
  q.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } };
  $("#composer").onsubmit = e => { e.preventDefault(); if (q.value.trim() && !busy && STATUS?.llm.ready) ask(q.value.trim()); };
  if (conversationId) loadConversation(conversationId); else renderHero();
  renderSetup(); loadConvs(); q.focus();
}
function renderHero() {
  $("#thread").innerHTML = `<div class="hero"><h1>Ce vă spune legea?</h1>
    <p>Întrebați în cuvinte simple. Asistentul identifică ramura de drept, citește doar actele potrivite și răspunde cu trimiteri la lege.</p>
    <div id="setupSlot"></div>
    <div class="examples">${EXAMPLES.map(([t, q]) => `<button class="example" data-q="${esc(q)}"><b>${t}</b>${esc(q)}</button>`).join("")}</div></div>`;
  document.querySelectorAll(".example").forEach(b => b.onclick = () => { $("#q").value = b.dataset.q; $("#q").focus(); $("#q").oninput(); });
}
async function loadConversation(id) {
  const msgs = await api(`/api/conversations/${id}`);
  const t = $("#thread"); t.innerHTML = `<div id="setupSlot"></div>`;
  for (const m of msgs) {
    if (m.role === "user") t.insertAdjacentHTML("beforeend", `<div class="msg-user">${esc(m.content)}</div>`);
    else {
      const el = aiBlock(); t.append(el);
      $(".answer", el).innerHTML = md(m.content);
      finishBlock(el, m.meta || {});
    }
  }
  bindCites(t); renderSetup(); $("#scroll").scrollTop = 1e9;
}
function aiBlock() {
  const d = document.createElement("div"); d.className = "msg-ai";
  d.innerHTML = `<div class="trace" hidden></div><details class="think" hidden><summary>Raționamentul modelului</summary><pre></pre></details>
    <div class="answer"></div><div class="notes"></div><div class="srcs"></div>`;
  return d;
}
function renderSource(s) {
  const links = [
    `<a href="#act/${s.id}">Deschide în aplicație</a>`,
    s.legis_url ? `<a href="${esc(s.legis_url)}" target="_blank" rel="noopener">legis.md ↗</a>` : "",
    s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">Monitorul Oficial ↗</a>` : "",
  ].filter(Boolean).join(" · ");
  const am = (s.amended_by || []).length ? `<div class="meta">Modificat ulterior prin: ${s.amended_by.map(a => `<a href="#act/${a.id}" title="${esc(a.title)}">${esc(a.ref)}</a>`).join(", ")}</div>` : "";
  return `<div class="source" id="src-${s.n}"><div class="source-top"><span class="source-n">S${s.n}</span><div style="flex:1;min-width:0">
    <div class="source-title"><a href="#act/${s.id}">${esc(s.ref)}${s.art ? `, art. ${esc(s.art)}` : ""}</a> · ${esc(s.title)}</div>
    ${s.amends ? `<div class="meta"><span class="badge b-fut">Modifică ${esc(s.amends)}</span></div>` : ""}
    <div class="meta">${statusBadge(s)}<span class="badge b-neutral">${s.consolidated ? "Text consolidat" : "Text publicat inițial"}</span><span>${s.version_effective ? "versiune din " + fmtDate(s.version_effective) : ""}</span></div>
    ${am}<div class="meta">${links}</div>
    <details><summary>Textul citat</summary><div class="excerpt">${esc(s.excerpt)}</div></details></div></div></div>`;
}
function finishBlock(el, meta) {
  const notes = $(".notes", el); notes.innerHTML = "";
  if (meta.future?.length) notes.insertAdjacentHTML("beforeend", `<div class="notice future"><b>Modificări anunțate pentru viitor</b> — azi nu se aplică, dar pot schimba răspunsul:<ul>${meta.future.map(f => `<li>De la <b>${fmtDate(f.effective)}</b>: <a href="#act/${f.id}">${esc(f.ref)}</a>, ${esc(f.title.slice(0, 110))}</li>`).join("")}</ul></div>`);
  (meta.warnings || []).forEach(w => notes.insertAdjacentHTML("beforeend", `<div class="notice warn">${esc(w)}</div>`));
  if (meta.sources?.length) $(".srcs", el).innerHTML = `<div class="sec-label">Surse</div>${meta.sources.map(renderSource).join("")}`;
  if (meta.thinking) { const th = $(".think", el); th.hidden = false; $("pre", th).textContent = meta.thinking; }
  $(".srcs", el).insertAdjacentHTML("beforeend", `<div class="disclaimer-foot">Informație generală generată automat, nu consultanță juridică. Verificați textul oficial și, pentru decizii importante, consultați un avocat.</div>`);
}
function bindCites(root) {
  root.onclick = e => {
    const b = e.target.closest(".cite"); if (!b) return;
    const block = b.closest(".msg-ai"); const s = $(`#src-${b.dataset.s}`, block); if (!s) return;
    s.scrollIntoView({ behavior: "smooth", block: "center" }); s.classList.add("flash"); setTimeout(() => s.classList.remove("flash"), 1600);
    const d = $("details", s); if (d) d.open = true;
  };
}
function renderStep(trace, ev) {
  trace.hidden = false;
  let el = trace.querySelector(`[data-id="${ev.id}"]`);
  if (!el) { el = document.createElement("div"); el.dataset.id = ev.id; trace.append(el); }
  el.className = "tstep " + ev.status;
  const detail = ev.detail ? `: <span class="${ev.id === "route" ? "crumb" : ""}">${esc(ev.detail)}</span>` : "";
  el.innerHTML = `<b>${esc(ev.label)}</b>${detail}${ev.note ? `<span class="note">${esc(ev.note)}</span>` : ""}${ev.items?.length ? `<ul>${ev.items.map(i => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}`;
}
async function ask(question) {
  busy = true; const send = $("#sendBtn"); send.disabled = true;
  const thread = $("#thread"); if ($(".hero", thread)) thread.innerHTML = `<div id="setupSlot"></div>`;
  thread.insertAdjacentHTML("beforeend", `<div class="msg-user">${esc(question)}</div>`);
  const el = aiBlock(); thread.append(el); bindCites(thread);
  const q = $("#q"); q.value = ""; q.style.height = "auto";
  const scroll = $("#scroll"); const stick = () => { if (scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 200) scroll.scrollTop = scroll.scrollHeight; };
  scroll.scrollTop = scroll.scrollHeight;
  const trace = $(".trace", el), ans = $(".answer", el), thinkBox = $(".think", el);
  let text = "", thinking = "", meta = {}, pending = false;
  const paint = () => { pending = false; ans.innerHTML = md(text); ans.classList.add("cursor"); stick(); };
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, conversation_id: conversationId }) });
    if (!r.ok || !r.body) throw new Error("Serverul nu a răspuns.");
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith("data: ")) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === "conversation") {
          if (conversationId !== ev.id) { conversationId = ev.id; history.replaceState(null, "", `#chat/${ev.id}`); loadConvs(); }
        } else if (ev.type === "step") renderStep(trace, ev);
        else if (ev.type === "thinking") { thinkBox.hidden = false; thinking += ev.text; $("pre", thinkBox).textContent = thinking; const p = $("pre", thinkBox); p.scrollTop = p.scrollHeight; }
        else if (ev.type === "token") { text += ev.text; if (!pending) { pending = true; requestAnimationFrame(paint); } }
        else if (ev.type === "sources") meta.sources = ev.items;
        else if (ev.type === "warnings") meta.warnings = ev.items;
        else if (ev.type === "future") meta.future = ev.items;
        else if (ev.type === "error") $(".notes", el).insertAdjacentHTML("beforeend", `<div class="notice err">${esc(ev.message)}</div>`);
        else if (ev.type === "done") meta = { sources: ev.sources, warnings: ev.warnings, future: ev.future, thinking: ev.thinking };
      }
    }
    if (meta.sources || meta.warnings) { ans.innerHTML = md(text); finishBlock(el, meta); }
  } catch (e) { $(".notes", el).insertAdjacentHTML("beforeend", `<div class="notice err">${esc(e.message)}</div>`); }
  ans.innerHTML = md(text); ans.classList.remove("cursor");
  busy = false; send.disabled = !STATUS?.llm.ready; q.focus(); stick();
}
async function loadConvs() {
  const list = await api("/api/conversations").catch(() => []);
  const el = $("#convList");
  el.innerHTML = list.map(c => `<div class="conv ${c.id === conversationId ? "on" : ""}"><a href="#chat/${c.id}" title="${esc(c.title)}">${esc(c.title)}</a><button data-del="${c.id}" aria-label="Șterge discuția">×</button></div>`).join("");
  el.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    await api(`/api/conversations/${b.dataset.del}`, { method: "DELETE" });
    if (+b.dataset.del === conversationId) location.hash = "#chat"; loadConvs();
  });
}
$("#newChat").onclick = () => { if (location.hash === "#chat") viewChat(); else location.hash = "#chat"; };

/* ---- search ---- */
function highlight(text, q) {
  const safe = esc(text); const terms = (q.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").match(/[a-z0-9]{4,}/g) || []);
  if (!terms.length) return safe;
  const fold = safe.normalize("NFD"); if (fold.length === 0) return safe;
  return safe.replace(/[\p{L}\p{N}]+/gu, w => {
    const f = w.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    return terms.some(t => f.startsWith(t.slice(0, Math.max(4, t.length - 2)))) ? `<mark>${w}</mark>` : w;
  });
}
const searchState = { q: "", kind: "", status: "", domain: "", year_from: "", year_to: "", page: 1 };
let treeCache = null;
async function getTree() { return treeCache || (treeCache = await api("/api/tree")); }
async function viewSearch() {
  const t = await getTree();
  const opts = t.flatMap(g => g.domains.map(d => `<option value="${d.id}">${esc(g.name)} › ${esc(d.name)}</option>`)).join("");
  main.innerHTML = `<div class="page"><h1 class="page-title">Caută în lege</h1>
    <p class="page-sub">Căutare în toate actele indexate: titluri și text integral. Merg și cuvinte fără diacritice sau referințe de tipul <code>95/2021</code>.</p>
    <form class="searchbar" id="sf"><input id="sq" placeholder="ex. concediu de maternitate, bagaj călători, 154/2003" value="${esc(searchState.q)}" aria-label="Căutare"><button>Caută</button></form>
    <div class="filters">
      <select id="fKind" aria-label="Tip"><option value="">Toate tipurile</option>${Object.entries(KIND).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
      <select id="fStatus" aria-label="Stare"><option value="">Orice stare</option><option value="presupus_in_vigoare">Fără abrogare detectată</option><option value="abrogat">Abrogate</option><option value="abrogare_viitoare">Vor fi abrogate</option></select>
      <select id="fDom" aria-label="Domeniu"><option value="">Toate domeniile</option>${opts}</select>
      <input class="input small" id="fFrom" type="number" placeholder="Din anul" aria-label="Din anul" value="${esc(searchState.year_from)}">
      <input class="input small" id="fTo" type="number" placeholder="Până în" aria-label="Până în anul" value="${esc(searchState.year_to)}">
    </div><div id="results"></div></div>`;
  $("#fKind").value = searchState.kind; $("#fStatus").value = searchState.status; $("#fDom").value = searchState.domain;
  const read = () => Object.assign(searchState, { q: $("#sq").value.trim(), kind: $("#fKind").value, status: $("#fStatus").value, domain: $("#fDom").value, year_from: $("#fFrom").value, year_to: $("#fTo").value });
  $("#sf").onsubmit = e => { e.preventDefault(); read(); searchState.page = 1; runSearch(); };
  ["fKind", "fStatus", "fDom", "fFrom", "fTo"].forEach(id => $("#" + id).onchange = () => { read(); searchState.page = 1; runSearch(); });
  if (searchState.q || searchState.domain) runSearch(); else $("#results").innerHTML = `<div class="empty">Introduceți un termen de căutare.</div>`;
  $("#sq").focus();
}
async function runSearch() {
  const p = new URLSearchParams(Object.entries(searchState).filter(([, v]) => v)); const box = $("#results");
  box.innerHTML = `<div class="empty">Caut…</div>`;
  try {
    const r = await api("/api/search?" + p);
    if (!r.results.length) { box.innerHTML = `<div class="empty">Niciun rezultat. Indexarea poate fi încă în curs; vedeți secțiunea Index.</div>`; return; }
    box.innerHTML = `<div class="lawtext-info">${fmtNum(r.total)} rezultate</div>` + r.results.map(a => `<article class="result"><h3><a href="#act/${a.id}">${esc(a.title)}</a></h3>
      <div class="meta"><b>${esc(a.ref)}</b>${a.adopted ? `<span>${fmtDate(a.adopted)}</span>` : ""}${statusBadge(a)}${a.has_text ? "" : '<span class="badge b-neutral">Doar titlu</span>'}${a.art ? `<span>art. ${esc(a.art)}</span>` : ""}</div>
      ${a.snippet ? `<p>${highlight(a.snippet, searchState.q)}</p>` : ""}</article>`).join("") +
      `<div class="pager"><button class="btn" id="pp" ${r.page <= 1 ? "disabled" : ""}>Înapoi</button><span>Pagina ${r.page} din ${Math.max(1, Math.ceil(r.total / 20))}</span><button class="btn" id="pn" ${r.page * 20 >= r.total ? "disabled" : ""}>Înainte</button></div>`;
    $("#pp").onclick = () => { searchState.page--; runSearch(); main.scrollTop = 0; };
    $("#pn").onclick = () => { searchState.page++; runSearch(); main.scrollTop = 0; };
  } catch (e) { box.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
}

/* ---- tree ---- */
async function viewTree() {
  treeCache = null; const t = await getTree();
  main.innerHTML = `<div class="page"><h1 class="page-title">Arborele legislației</h1>
    <p class="page-sub">Așa navighează asistentul: mai întâi ramura, apoi domeniul, apoi actele, apoi articolele. Punctul verde arată un act de bază cu text indexat.</p>
    ${t.map(g => `<section class="branch"><h2>${esc(g.name)}</h2><div class="domains">${g.domains.map(d => `<div class="domain">
      <h3><a href="#search" data-dom="${d.id}">${esc(d.name)}</a><small>${fmtNum(d.count)} acte</small></h3><p>${esc(d.desc)}</p>
      ${d.key_acts.map(a => `<div class="keyact"><span class="dotx ${a.has_text ? "has" : ""}" title="${a.has_text ? "Text indexat" : "Text neindexat încă"}"></span><a href="#act/${a.id}">${esc(a.title)}</a></div>`).join("")}</div>`).join("")}</div></section>`).join("")}</div>`;
  main.querySelectorAll("[data-dom]").forEach(a => a.onclick = () => { Object.assign(searchState, { q: "", kind: "", status: "", domain: a.dataset.dom, year_from: "", year_to: "", page: 1 }); });
}

/* ---- act ---- */
async function viewAct(id) {
  main.innerHTML = `<div class="page"><div class="empty">Se încarcă…</div></div>`;
  let a; try { a = await api(`/api/acts/${id}`); } catch (e) { main.innerHTML = `<div class="page"><div class="notice err">${esc(e.message)}</div></div>`; return; }
  const rel = { modifica: "modifică", abroga: "abrogă" };
  const links = [a.legis_url && `<a class="btn" href="${esc(a.legis_url)}" target="_blank" rel="noopener">legis.md ↗</a>`, a.url && `<a class="btn" href="${esc(a.url)}" target="_blank" rel="noopener">Monitorul Oficial ↗</a>`].filter(Boolean).join(" ");
  main.innerHTML = `<div class="page"><div class="act-head">
      <div class="meta">${a.domains.map(d => `<span>${esc(d.group)} › ${esc(d.name)}</span>`).join(" · ")}</div>
      <h1>${esc(a.title)}</h1>
      <div class="meta"><b>${esc(a.ref)}</b><span>Adoptat: ${fmtDate(a.adopted)}</span>${a.published ? `<span>Publicat: ${fmtDate(a.published)}</span>` : ""}${statusBadge(a)}<span class="badge b-neutral">${!a.has_text ? "Doar titlu" : a.consolidated ? "Text consolidat" : "Text publicat inițial"}</span></div>
      <div class="row-actions" style="margin:12px 0 16px">${links}</div></div>
    ${a.status !== "presupus_in_vigoare" ? `<div class="notice ${a.status === "abrogat" ? "err" : "warn"}"><b>${a.status === "abrogat" ? "Acest act a fost abrogat" : "Acest act va fi abrogat"}</b>${a.status_date ? ` (${fmtDate(a.status_date)})` : ""}. ${esc(a.status_note || "")}${a.replaced_by ? `<br>Înlocuit de: <a href="#act/${a.replaced_by.id}">${esc(a.replaced_by.ref)}, ${esc(a.replaced_by.title)}</a>` : ""}</div>` : ""}
    ${a.future_effective ? `<div class="notice warn">Acest act sau unele prevederi ale lui intră în vigoare la <b>${fmtDate(a.future_effective)}</b>. Verificați dispozițiile finale.</div>` : ""}
    ${!a.consolidated && a.has_text ? `<div class="notice warn">Acesta este textul publicat inițial. ${a.amended_by.length ? "Modificările de mai jos nu sunt integrate în text." : ""} Pentru redacția curentă, descărcați PDF-ul de pe legis.md și importați-l în secțiunea Index.</div>` : ""}
    ${a.versions.length ? `<div class="panel"><h3>Versiuni</h3><ul class="tl">${a.versions.map(v => `<li>${fmtDate(v.effective)} · ${esc(v.label || "")}${v.current ? ' <span class="badge b-in">Curentă</span>' : (v.effective > new Date().toISOString().slice(0, 10) ? ' <span class="badge b-fut">Viitoare</span>' : "")}</li>`).join("")}</ul></div>` : ""}
    ${a.amended_by.length ? `<div class="panel"><h3>Modificat de (${a.amended_by.length})</h3><ul class="tl">${a.amended_by.map(x => `<li>${fmtDate(x.effective)} · ${rel[x.rel] === "abrogă" ? `<span class="badge ${x.whole ? "b-ab" : "b-fut"}">${x.whole ? "Abrogare" : "Abrogare parțială"}</span> ` : ""}<a href="#act/${x.id}">${esc(x.ref)}</a>, ${esc(x.title.slice(0, 110))}</li>`).join("")}</ul></div>` : ""}
    ${a.targets.length ? `<div class="panel"><h3>Acest act afectează</h3><ul class="tl">${a.targets.map(t => `<li>${rel[t.rel]}${t.rel === "abroga" && !t.whole ? " parțial" : ""} ${t.id ? `<a href="#act/${t.id}">${esc(t.title)}</a> (${esc(t.label)})` : esc(t.label)}</li>`).join("")}</ul></div>` : ""}
    <div class="sec-label">Textul actului</div>
    ${a.chunks.length ? `<input class="input search-in" id="findIn" placeholder="Filtrează articolele din acest act…" aria-label="Filtrează articolele"><div id="arts">${a.chunks.map(c => `<div class="article">${c.path ? `<span class="apath">${esc(c.path)}</span>` : ""}${esc(c.text)}</div>`).join("")}</div>` : `<div class="empty">Textul nu este încă indexat. ${a.legis_url ? `Îl găsiți pe <a href="${esc(a.legis_url)}" target="_blank" rel="noopener">legis.md</a>.` : ""}</div>`}
  </div>`;
  main.scrollTop = 0;
  const f = $("#findIn");
  if (f) f.oninput = () => { const v = f.value.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); document.querySelectorAll(".article").forEach(el => { el.hidden = v && !el.textContent.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").includes(v); }); };
}

/* ---- index ---- */
const STAGE = { pornire: "Pornire", "caut ediții noi": "Caut ediții noi", catalog: "Citesc catalogul ediților", "text integral": "Indexez textul integral", "la zi": "La zi", "pauză": "În pauză", "recitesc cuprinsurile": "Recitesc cuprinsurile edițiilor (o singură dată)", "curăț textele greșite": "Curăț textele citite greșit" };
function viewIndex() {
  main.innerHTML = `<div class="page"><h1 class="page-title">Indexul legislației</h1>
    <p class="page-sub">Indexarea rulează singură în fundal și continuă de unde a rămas. Întâi citește catalogul tuturor edițiilor Monitorului Oficial, apoi textul integral, începând cu codurile și legile.</p>
    <div id="live"></div>
    <div id="verifyBox"></div>
    <div class="panel"><h3>Sursa textelor și limitele ei</h3>
      <p style="margin:0 0 6px">Sursa automată este <a href="https://monitorul.gov.md" target="_blank" rel="noopener">Monitorul Oficial</a>, cu edițiile disponibile pe site. Textele sunt cele <b>publicate inițial</b>; modificările sunt legate ca acte separate, iar starea e detectată automat.</p>
      <p style="margin:0"><a href="https://www.legis.md" target="_blank" rel="noopener">legis.md</a> are redacțiile consolidate pe ani, dar blochează accesul automat, așa că programul nu îl descarcă singur. Actele mai vechi decât edițiile disponibile apar doar dacă le importați.</p></div>
    <div class="panel"><h3>Importă texte consolidate</h3>
      <p style="margin:0 0 9px">Pe legis.md deschideți actul, alegeți anul curent și apăsați <b>Pdf</b>, apoi trageți fișierul aici. Textul importat înlocuiește varianta publicată inițial în răspunsuri.</p>
      <div class="drop" id="drop">Trageți aici fișierele PDF sau <button class="link" id="pick">alegeți-le</button><input type="file" id="file" accept=".pdf,.txt" multiple hidden>
      <div style="margin-top:9px;font-size:13px"><label>În vigoare de la (opțional; o dată viitoare îl marchează ca versiune viitoare): <input class="input" type="date" id="eff"></label></div></div>
      <div id="importLog"></div></div></div>`;
  const drop = $("#drop"), file = $("#file");
  $("#pick").onclick = () => file.click();
  file.onchange = () => upload(file.files);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files); };
  renderIndexLive();
  renderVerify();
}

/* ---- index check ---- */
let VERIFY = null, verifyBusy = false;
const CHECK_ICON = { ok: ["✓", "b-in"], info: ["i", "b-neutral"], warn: ["!", "b-fut"], fail: ["✕", "b-ab"] };
function renderVerify() {
  const box = $("#verifyBox"); if (!box) return;
  const v = VERIFY;
  const summary = v ? `${v.summary.ok} în regulă · ${v.summary.info} de informat · ${v.summary.warn} de verificat · ${v.summary.fail} probleme · ${v.seconds} s` : "";
  const rows = v ? v.checks.map(c => {
    const [icon, cls] = CHECK_ICON[c.status];
    return `<details class="check ${c.status}"${c.status === "fail" || c.status === "warn" ? " open" : ""}>
      <summary><span class="badge ${cls}">${icon}</span><span class="check-label">${esc(c.label)}</span><b>${fmtNum(c.count)}</b></summary>
      <p>${esc(c.explain)}</p>${c.examples.length ? `<ul>${c.examples.map(e => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}</details>`;
  }).join("") : "";
  const repairable = v && v.checks.some(c => c.repairable);
  box.innerHTML = `<div class="panel"><div class="row-actions" style="justify-content:space-between"><div><b>Verificarea indexului</b>
      <span class="lawtext-info">${v ? summary : "Verifică într-un minut arborele, edițiile, textele și legăturile dintre acte."}</span></div>
      <div class="row-actions">${repairable ? `<button class="btn" id="repairBtn"${verifyBusy ? " disabled" : ""}>Repară automat</button>` : ""}
      <button class="btn primary" id="verifyBtn"${verifyBusy ? " disabled" : ""}>${verifyBusy ? "Verific…" : v ? "Verifică din nou" : "Verifică acum"}</button></div></div>
    ${rows ? `<div class="checks">${rows}</div>` : ""}</div>`;
  $("#verifyBtn").onclick = () => runVerify("/api/verify");
  if (repairable) $("#repairBtn").onclick = () => runVerify("/api/verify/repair", "POST");
}
async function runVerify(url, method) {
  verifyBusy = true; renderVerify();
  try {
    const r = await api(url, method ? { method } : undefined);
    VERIFY = r.check || r;
    if (r.fixed) toast(Object.keys(r.fixed).length ? "Reparat: " + Object.entries(r.fixed).map(([k, n]) => `${k} ${fmtNum(n)}`).join(", ") : "Nimic de reparat.");
  } catch (e) { toast(e.message); }
  verifyBusy = false; renderVerify();
}

async function upload(files) {
  const log = $("#importLog");
  for (const f of files) {
    const fd = new FormData(); fd.append("file", f); fd.append("effective", $("#eff").value);
    try {
      const r = await api("/api/import", { method: "POST", body: fd });
      log.insertAdjacentHTML("beforeend", `<div class="notice future">${esc(f.name)}: importat ca <a href="#act/${r.act_id}">${esc(r.title)}</a>${r.future ? `, versiune viitoare de la ${fmtDate(r.effective)}` : ""}.</div>`);
    } catch (e) { log.insertAdjacentHTML("beforeend", `<div class="notice err">${esc(f.name)}: ${esc(e.message)}</div>`); }
  }
}
function renderIndexLive() {
  const el = $("#live"); if (!el || !STATUS) return; const x = STATUS.index;
  const pct = x.issues_total ? 100 * x.issues_done / x.issues_total : 0;
  el.innerHTML = `<div class="stats">
      <div class="stat"><b>${fmtNum(x.acts)}</b><span>acte în catalog</span></div>
      <div class="stat"><b>${fmtNum(x.with_text)}</b><span>cu text integral</span></div>
      <div class="stat"><b>${fmtNum(x.consolidated)}</b><span>consolidate</span></div>
      <div class="stat"><b>${fmtNum(x.abrogated)}</b><span>abrogate detectate</span></div>
      <div class="stat"><b>${fmtNum(x.relations)}</b><span>legături între acte</span></div>
      <div class="stat"><b>${x.year_min ? `${x.year_min}–${x.year_max}` : "—"}</b><span>ani acoperiți cu text</span></div></div>
    <div class="panel"><div class="row-actions" style="justify-content:space-between"><div><b>${esc(STAGE[x.stage] || x.stage)}</b> ${x.current ? `· <span style="color:var(--ink-3)">${esc(x.current)}</span>` : ""}</div>
      <button class="btn" id="pauseBtn">${x.paused ? "Reia" : "Pauză"}</button></div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="lawtext-info" style="margin-top:5px">${fmtNum(x.issues_done)} din ${fmtNum(x.issues_total)} ediții cu text procesate (${pct.toFixed(1)}%)${x.issues.error ? ` · ${fmtNum(x.issues.error)} cu erori` : ""}${x.issues.no_text ? ` · ${fmtNum(x.issues.no_text)} fără text extractibil` : ""}</div>
      ${x.last_error ? `<div class="lawtext-info" style="margin-top:3px">Ultima eroare: ${esc(x.last_error.slice(0, 180))}</div>` : ""}</div>
    ${memoryHtml(STATUS.memory)}
    ${taggerHtml(STATUS.tagger)}`;
  $("#pauseBtn").onclick = async () => { await post(x.paused ? "/api/index/resume" : "/api/index/pause"); pollStatus(); };
  $("#tagBtn").onclick = async () => { await post(STATUS.tagger.paused ? "/api/tagger/resume" : "/api/tagger/pause"); pollStatus(); };
}
function memoryHtml(m) {
  if (!m) return "";
  const row = (label, used, total, note) => used == null || !total ? "" : `<div class="mem-row"><div class="mem-head"><b>${label}</b><span>${used.toFixed(1)} / ${total.toFixed(1)} GB${note ? ` · ${note}` : ""}</span></div>
    <div class="bar${used / total > 0.9 ? " hot" : ""}"><i style="width:${Math.min(100, 100 * used / total)}%"></i></div></div>`;
  // A model only partly on the GPU runs its other layers on the CPU, which is several times slower.
  const models = (m.models || []).map(x => `${esc(x.name)}: ${x.size_gb.toFixed(1)} GB, ${x.gpu_pct}% pe GPU${x.gpu_pct < 100 ? " (restul pe procesor, mai lent)" : ""}`);
  return `<div class="panel"><b>Memorie</b>
    ${row("RAM", m.ram_used, m.ram_total, m.engine_ram ? `motorul AI: ${m.engine_ram.toFixed(1)} GB` : "")}
    ${row(`VRAM${m.gpu ? ` (${esc(m.gpu)})` : ""}`, m.vram_used, m.vram_total, "")}
    <div class="lawtext-info" style="margin-top:6px">${models.length ? `Model încărcat: ${models.join(" · ")}` : "Niciun model încărcat în memorie."}</div></div>`;
}
const TAG_STATE = {
  etichetez: ["Rulează", "run"], "pauză": ["În pauză", "idle"], "aștept să se termine răspunsul": ["Așteaptă: se răspunde la o întrebare", "wait"],
  "aștept motorul AI": ["Așteaptă motorul AI", "wait"], "la zi": ["La zi", "ok"], pornire: ["Pornește", "wait"] };
const TAG_PHASE = { reasoning: "Raționează", answering: "Scrie răspunsul", fallback: "Reîncearcă fără raționament (a depășit limita)" };
const hours = h => h == null ? "—" : h < 1 ? "sub o oră" : h < 48 ? `~${Math.round(h)} ore` : `~${Math.round(h / 24)} zile`;
function taggerHtml(t) {
  const c = t.counts, s = t.session, total = c.done + c.pending;
  const pct = n => total ? 100 * n / total : 0;
  const [stateLabel, stateCls] = t.paused ? TAG_STATE["pauză"] : TAG_STATE[t.state] || [t.state, "wait"];
  const lv = t.live;
  const tokens = lv ? lv.thinking + lv.answer : 0;
  const live = lv ? `<div class="tag-live">
      <div class="tag-live-head"><b>Acum · ${esc(TAG_PHASE[lv.phase] || lv.phase)}</b>
        <span>${fmtNum(tokens)} / ${fmtNum(lv.limit)} tokeni${lv.tok_s ? ` · ${lv.tok_s.toFixed(1)} tok/s` : ""} · ${lv.elapsed} s</span></div>
      <div class="bar${tokens / lv.limit > 0.8 ? " hot" : ""}"><i style="width:${Math.min(100, 100 * tokens / lv.limit)}%"></i></div>
      <ol class="tag-batch">${lv.titles.map(x => `<li>${esc(x)}</li>`).join("")}</ol></div>`
    : `<div class="tag-live idle">${t.paused ? "Încadrarea este oprită. Apăsați Reia pentru a continua."
        : t.state === "la zi" ? "Toate actele au fost încadrate. Actele noi sunt preluate automat."
        : t.state === "aștept să se termine răspunsul" ? "Modelul răspunde la o întrebare; încadrarea continuă imediat după."
        : "Pregătesc următorul lot…"}</div>`;
  const recent = (t.recent || []).map(r => `<li><span class="tag-how ${r.how}">${r.how === "ai" ? "AI" : "fără raț."}</span>
      <span class="tag-title">${esc(r.title)}</span><span class="tag-doms">${r.domains.length ? r.domains.map(esc).join(", ") : "fără domeniu"}</span></li>`).join("");
  return `<div class="panel tagp">
    <div class="row-actions" style="justify-content:space-between"><div><b>Încadrare AI în domenii</b> <span class="pill ${stateCls}">${esc(stateLabel)}</span></div>
      <button class="btn" id="tagBtn">${t.paused ? "Reia" : "Pauză"}</button></div>
    <div class="stats tag-stats">
      <div class="stat"><b>${fmtNum(c.done)}</b><span>procesate · ${pct(c.done).toFixed(1)}%</span></div>
      <div class="stat"><b>${fmtNum(c.pending)}</b><span>rămase · ${fmtNum(c.pending_laws)} legi și coduri</span></div>
      <div class="stat"><b>${hours(t.eta_hours)}</b><span>timp estimat${t.eta_laws_hours != null && c.pending_laws ? ` · legi: ${hours(t.eta_laws_hours)}` : ""}</span></div></div>
    <div class="bar split" title="cu raționament / fără raționament / după regulă">
      <i style="width:${pct(c.reasoned)}%"></i><i class="b2" style="width:${pct(c.fallback)}%"></i><i class="b3" style="width:${pct(c.by_rule)}%"></i></div>
    <div class="tag-legend"><span><i></i>cu raționament ${fmtNum(c.reasoned)}</span><span><i class="b2"></i>fără raționament ${fmtNum(c.fallback)}</span>
      <span><i class="b3"></i>acte individuale, după regulă ${fmtNum(c.by_rule)}</span>${c.unclassified ? `<span>neclasificate ${fmtNum(c.unclassified)}</span>` : ""}
      <span>cu domeniu ${fmtNum(c.with_domain)} · fără ${fmtNum(c.done - c.with_domain)}</span></div>
    ${live}
    <div class="tag-metrics">
      <div><b>${s.tok_s ? s.tok_s.toFixed(1) : "—"}</b><span>tokeni/s, medie</span></div>
      <div><b>${s.sec_per_act ? s.sec_per_act.toFixed(1) + " s" : "—"}</b><span>timp mediu per act</span></div>
      <div><b>${fmtNum(s.acts)}</b><span>acte de la pornire · ${fmtNum(s.batches)} loturi</span></div>
      <div><b>${fmtNum(s.fallback)} / ${fmtNum(s.failed)}</b><span>loturi fără raționament / eșuate</span></div></div>
    ${recent ? `<div class="sec-label" style="margin:12px 0 4px">Ultimele încadrări</div><ul class="tag-recent">${recent}</ul>` : ""}
    <div class="lawtext-info" style="margin-top:8px">Rulează doar când nu răspundeți la o întrebare, legile și codurile primele.${t.model ? ` Model: ${esc(t.model)}.` : ""}</div>
    ${t.last_error ? `<div class="lawtext-info" style="margin-top:3px">Ultima eroare: ${esc(t.last_error.slice(0, 180))}</div>` : ""}</div>`;
}

/* ---- problems ---- */
const PROBLEM = { error: ["Eroare de descărcare", "b-ab"], no_text: ["Fără text extractibil", "b-fut"], retry: ["Reîncercare programată", "b-neutral"] };
function viewProblems() {
  main.innerHTML = `<div class="page"><h1 class="page-title">Probleme de indexare</h1>
    <p class="page-sub">Edițiile Monitorului Oficial care nu au putut fi procesate. Erorile de descărcare se reîncearcă singure la fiecare circa 6 ore; de aici le puteți relua imediat. Edițiile fără text sunt de obicei scanate, așa că reîncercarea poate da același rezultat.</p>
    <div id="pLive"><div class="empty">Se încarcă…</div></div></div>`;
  renderProblems(); modelsTimer = setInterval(renderProblems, 5000);
}
async function renderProblems() {
  const box = $("#pLive"); if (!box) return;
  const p = await api("/api/problems").catch(() => null); if (!p) return;
  const gaps = `<p class="lawtext-info" style="margin-top:12px">${fmtNum(p.missing)} numere de ediție nu există pe site. Sunt goluri normale în numerotare și nu se reîncearcă.</p>`;
  if (!p.issues.length) { box.innerHTML = `<div class="empty">Nicio problemă în acest moment.</div>${gaps}`; return; }
  box.innerHTML = `<div class="row-actions" style="justify-content:space-between;margin-bottom:12px"><b>${fmtNum(p.issues.length)} ediții cu probleme</b><button class="btn primary" id="retryAll">Reîncearcă toate</button></div>
    ${p.issues.map(i => { const [label, cls] = PROBLEM[i.status]; return `<div class="model"><div class="model-info"><b>${esc(i.title || "Ediția #" + i.id)}</b> <span class="badge ${cls}">${label}</span>
      <p>${fmtNum(i.n_acts)} acte listate${i.attempts ? ` · ${fmtNum(i.attempts)} încercări` : ""} · <a href="https://monitorul.gov.md/ro/monitor/${i.id}" target="_blank" rel="noopener">Deschide ediția ↗</a></p>
      ${i.error ? `<p style="overflow-wrap:anywhere">${esc(i.error)}</p>` : ""}</div><button class="btn" data-retry="${i.id}">Reîncearcă</button></div>`; }).join("")}${gaps}`;
  $("#retryAll").onclick = async () => { const r = await post("/api/problems/retry-all"); toast(`${fmtNum(r.queued)} ediții puse la rând.`); renderProblems(); pollStatus(); };
  box.querySelectorAll("[data-retry]").forEach(b => b.onclick = async () => {
    try { await post(`/api/problems/${b.dataset.retry}/retry`); toast("Ediția a fost pusă prima la rând."); } catch (e) { toast(e.message); }
    renderProblems(); pollStatus();
  });
}

/* ---- models ---- */
let modelsTimer = null;
function viewModels() {
  main.innerHTML = `<div class="page"><h1 class="page-title">Modele AI</h1>
    <p class="page-sub">Modelele rulează local, pe calculatorul dvs. Le puteți schimba, șterge sau adăuga oricând. Pentru dreptul moldovenesc, alegeți unul cu raționament.</p>
    <div id="mLive"></div>
    <div class="sec-label">Adaugă alt model</div>
    <form class="searchbar" id="addM"><input id="mName" placeholder="ex. mistral-large-4:latest sau hf.co/utilizator/model-GGUF" aria-label="Nume model"><button>Descarcă</button></form>
    <p class="lawtext-info" style="margin-top:6px">Orice nume din <a href="https://ollama.com/library" target="_blank" rel="noopener">biblioteca Ollama</a> sau un model GGUF de pe Hugging Face.</p></div>`;
  $("#addM").onsubmit = async e => { e.preventDefault(); const n = $("#mName").value.trim(); if (!n) return; try { await post("/api/models/pull", { name: n }); $("#mName").value = ""; renderModels(); } catch (err) { toast(err.message); } };
  renderModels(); modelsTimer = setInterval(renderModels, 2000);
}
async function renderModels() {
  const box = $("#mLive"); if (!box) return;
  const m = await api("/api/models").catch(() => null); if (!m) return;
  const inst = new Set(m.installed.map(x => x.name)); const pulls = Object.fromEntries(m.pulls.filter(p => p.state === "descarc" || p.state === "eroare").map(p => [p.name, p]));
  const pullRow = p => p.state === "eroare" ? `<div class="notice err" style="margin:5px 0 0">${esc(p.error)}</div>` : `<div class="lawtext-info">${esc(p.status)} · ${Math.floor(p.progress * 100)}%</div><div class="bar"><i style="width:${p.progress * 100}%"></i></div>`;
  const loaded = n => m.loaded.find(l => l.name === n);
  const extra = m.pulls.filter(p => p.state === "descarc" && !inst.has(p.name) && !m.catalog.some(c => c.name === p.name));
  const stopped = m.state === "oprit";
  const memoryBtn = x => {
    const n = esc(x.name);
    if (x.name in m.loading && m.loading[x.name] === null) return `<button class="btn" disabled>Se încarcă…</button>`;
    return loaded(x.name) ? `<button class="btn" data-unload="${n}">Scoate din memorie</button>` : `<button class="btn" data-load="${n}">Încarcă în memorie</button>`;
  };
  const memoryText = x => {
    const l = loaded(x.name); if (!l) return "";
    return ` · <b>în memorie</b>, ${l.size_gb} GB, ${l.gpu_pct}% pe GPU${m.pinned.includes(x.name) ? ", rămâne încărcat" : ", se eliberează după 30 min de inactivitate"}`;
  };
  box.innerHTML = `<div class="sec-label">Motor AI</div>
    <div class="model"><div class="model-info"><b>${stopped ? "Oprit" : m.state === "gata" ? "Pornit" : "Se pornește…"}</b>
      <p>${stopped ? "Nu folosește memorie. Căutarea și indexarea funcționează în continuare." : "Oprirea eliberează toată memoria folosită de AI, inclusiv modelul încărcat. Căutarea și indexarea continuă."}</p></div>
      ${stopped ? `<button class="btn primary" id="engineStart">Pornește motorul</button>` : `<button class="btn" id="engineStop" ${m.state === "gata" ? "" : "disabled"}>Oprește motorul</button>`}</div>
    <div class="sec-label">Instalate</div>
    ${stopped ? `<div class="empty">Porniți motorul AI pentru a vedea și gestiona modelele.</div>` : m.installed.length ? m.installed.map(x => `<div class="model ${x.name === m.active ? "active" : ""}"><div class="model-info"><b>${esc(x.name)}</b> ${x.name === m.active ? '<span class="badge b-in">Activ</span>' : ""}
      <p>${x.size_gb} GB${x.params ? " · " + esc(x.params) : ""}${x.quant ? " · " + esc(x.quant) : ""}${memoryText(x)}</p>
      ${m.loading[x.name] ? `<div class="notice err" style="margin:5px 0 0">${esc(m.loading[x.name])}</div>` : ""}</div>
      <div class="row-actions">${memoryBtn(x)}${x.name !== m.active ? `<button class="btn primary" data-act="${esc(x.name)}">Folosește</button>` : ""}<button class="btn danger" data-del="${esc(x.name)}">Șterge</button></div></div>`).join("") : `<div class="empty">Niciun model instalat încă.</div>`}
    ${extra.map(p => `<div class="model"><div class="model-info"><b>${esc(p.name)}</b>${pullRow(p)}</div><button class="btn" data-cancel="${esc(p.name)}">Anulează</button></div>`).join("")}
    ${stopped ? "" : `<div class="sec-label">Recomandate</div>`}
    ${stopped ? "" : m.catalog.filter(c => !inst.has(c.name)).map(c => `<div class="model"><div class="model-info"><b>${esc(c.title)}</b> <small style="color:var(--ink-3)">${c.size_gb} GB</small><p>${esc(c.note)}</p>${pulls[c.name] ? pullRow(pulls[c.name]) : ""}</div>
      ${pulls[c.name]?.state === "descarc" ? `<button class="btn" data-cancel="${esc(c.name)}">Anulează</button>` : `<button class="btn" data-pull="${esc(c.name)}">Descarcă</button>`}</div>`).join("")}`;
  const action = (el, url, body) => { if (el) el.onclick = async () => { el.disabled = true; try { await post(url, body); } catch (e) { toast(e.message); } renderModels(); pollStatus(); }; };
  action($("#engineStop"), "/api/engine/stop");
  action($("#engineStart"), "/api/engine/start");
  box.querySelectorAll("[data-load]").forEach(b => action(b, "/api/models/load", { name: b.dataset.load }));
  box.querySelectorAll("[data-unload]").forEach(b => action(b, "/api/models/unload", { name: b.dataset.unload }));
  box.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => { await post("/api/models/active", { name: b.dataset.act }); renderModels(); pollStatus(); });
  box.querySelectorAll("[data-pull]").forEach(b => b.onclick = async () => { await post("/api/models/pull", { name: b.dataset.pull }); renderModels(); });
  box.querySelectorAll("[data-cancel]").forEach(b => b.onclick = async () => { await post("/api/models/cancel", { name: b.dataset.cancel }); renderModels(); });
  box.querySelectorAll("[data-del]").forEach(b => b.onclick = () => confirmDelete(b.dataset.del));
}
function confirmDelete(name) {
  const m = $("#modal");
  m.innerHTML = `<div class="dialog" role="dialog" aria-modal="true"><h2>Ștergeți modelul?</h2><p><b>${esc(name)}</b> va fi șters de pe disc. Îl puteți descărca din nou oricând.</p><div class="dialog-foot"><button class="btn" id="no">Renunță</button><button class="btn primary" id="yes">Șterge</button></div></div>`;
  m.hidden = false; $("#no").onclick = () => m.hidden = true;
  $("#yes").onclick = async () => { m.hidden = true; try { await api(`/api/models/${encodeURIComponent(name)}`, { method: "DELETE" }); renderModels(); pollStatus(); } catch (e) { toast(e.message); } };
}

/* ---- boot ---- */
(async () => {
  go(); await pollStatus();
  if (STATUS && !STATUS.disclaimer_accepted) showDisclaimer(true);
})();
