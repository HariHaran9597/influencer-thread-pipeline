/* ThreadForge — frontend logic
   Streams the backend NDJSON feed into a live pipeline rail, then renders
   platform-NATIVE preview cards (LinkedIn / X / Instagram) with fit meters.
   BYOK: keys live in localStorage and ride along per request only. */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const NODES = [
  ["researcher", "Researching live sources"],
  ["guardrail_sources", "Scanning bundle for injections"],
  ["writer", "Writing cited drafts"],
  ["fact_checker", "Fact-checking every claim"],
  ["editor", "Editor review loop"],
  ["formatter", "Formatting per platform"],
  ["guardrail_outputs", "PII + citation guardrails"],
  ["virality", "Scoring viral potential"],
  ["tracker", "Sealing the audit trail"],
];

let settings = JSON.parse(localStorage.getItem("itp_settings") || "{}");
let lastResult = null;
let activePlatform = "linkedin";
let topicUsed = "";
let viralityData = {};

/* ═══════════ settings / health ═══════════ */
function saveSettings() {
  settings.groqKey = $("#key-groq").value.trim() || undefined;
  settings.tavilyKey = $("#key-tavily").value.trim() || undefined;
  settings.backendUrl = $("#backend-url").value.trim().replace(/\/+$/, "") || undefined;
  localStorage.setItem("itp_settings", JSON.stringify(settings));
  fillForm();
  $("#settings-note").textContent = "Saved — keys stay in this browser only.";
  probeHealth();
}
function clearSettings() {
  localStorage.removeItem("itp_settings");
  settings = {};
  fillForm();
  $("#settings-note").textContent = "Keys cleared — runs will use mock mode.";
  probeHealth();
}
function fillForm() {
  $("#key-groq").value = settings.groqKey || "";
  $("#key-tavily").value = settings.tavilyKey || "";
  $("#backend-url").value = settings.backendUrl || "";
}

async function probeHealth() {
  const chip = $("#mode-chip");
  try {
    const h = await (await fetch(`${settings.backendUrl || ""}/api/health`)).json();
    const live = h.llm === "live";
    chip.textContent = live ? `● live · ${h.writer_model}` : "● mock mode";
    chip.className = `chip ${live ? "live" : "mock"}`;
  } catch {
    chip.textContent = "● backend unreachable";
    chip.className = "chip mock";
  }
}

/* ═══════════ drawers ═══════════ */
function openDrawer(id) {
  $(id).classList.remove("hidden");
  $("#drawer-overlay").classList.remove("hidden");
}
function closeDrawers() {
  ["#settings", "#audit"].forEach((id) => $(id).classList.add("hidden"));
  $("#drawer-overlay").classList.add("hidden");
}
$("#drawer-overlay").addEventListener("click", closeDrawers);
$("#btn-close-settings").addEventListener("click", closeDrawers);
$("#btn-close-audit").addEventListener("click", closeDrawers);
$("#btn-settings").addEventListener("click", () => openDrawer("#settings"));
$("#btn-audit").addEventListener("click", () => openDrawer("#audit"));
$("#audit-tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".seg-btn");
  if (!b) return;
  $$("#audit-tabs .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  $$(".audit-pane").forEach((p) => p.classList.toggle("active", p.dataset.pane === b.dataset.pane));
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawers(); });

/* ═══════════ run ═══════════ */
$("#btn-run").addEventListener("click", runPipeline);
$("#topic").addEventListener("keydown", (e) => { if (e.key === "Enter") runPipeline(); });
$("#btn-regenerate").addEventListener("click", runPipeline);

function buildRail() {
  $("#rail-track").innerHTML = NODES.map(([id, label]) =>
    `<div class="rail-step" data-node="${id}"><span class="rail-dot"></span>${label}<span class="rail-meta"></span></div>`
  ).join("");
}

async function runPipeline() {
  const topic = $("#topic").value.trim();
  if (topic.length < 3) { $("#topic").focus(); return; }
  topicUsed = topic;

  const btn = $("#btn-run"), label = $("#btn-run-label");
  btn.disabled = true; label.textContent = "Running…";
  $("#results").classList.add("hidden");
  $("#rail").classList.remove("hidden");
  $("#live-log").classList.remove("hidden");
  $("#live-log").textContent = "";
  buildRail();
  closeDrawers();
  $("#hero").scrollIntoView({ block: "start" });

  try {
    const resp = await fetch(`${settings.backendUrl || ""}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic,
        platforms: $$(".pick input:checked").map((i) => i.value),
        groq_key: settings.groqKey || null,
        tavily_key: settings.tavilyKey || null,
      }),
    });
    if (!resp.ok || !resp.body) throw new Error(`backend ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) handleEvent(JSON.parse(line));
      }
    }
  } catch (err) {
    logLine(`✗ ${err.message} — check Keys → Backend URL`);
  } finally {
    btn.disabled = false; label.textContent = "Generate";
  }
}

function handleEvent(ev) {
  if (ev.type === "mode") {
    logLine(`· pipeline: ${ev.llm === "live" ? "LIVE" : "MOCK"} llm · ${ev.search} search`);
    return;
  }
  if (ev.type === "step") {
    const step = $(`.rail-step[data-node="${ev.node}"]`);
    if (step) {
      $$(".rail-step").forEach((s) => {
        if (s.classList.contains("active") && s !== step) s.classList.replace("active", "done");
      });
      step.classList.add("active");
      const t = (ev.telemetry || [])[0];
      if (t?.detail) step.querySelector(".rail-meta").textContent = t.detail;
    }
    (ev.log || []).forEach(logLine);
    return;
  }
  if (ev.type === "result") {
    lastResult = ev;
    try { localStorage.setItem("itp_last_run", JSON.stringify({ topic: topicUsed, ev })); } catch {}
    $$(".rail-step").forEach((s) => s.classList.replace("active", "done"));
    renderWorkspace(ev);
    $("#results").classList.remove("hidden");
    setTimeout(() => $("#results").scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  }
}

function logLine(text) {
  const box = $("#live-log");
  box.textContent += text + "\n";
  box.scrollTop = box.scrollHeight;
}

/* ═══════════ workspace rendering ═══════════ */
function renderWorkspace(ev) {
  const { summary, outputs, sources, claim_results, editor_critiques, guardrail_outputs, guardrail_sources, log } = ev;
  viralityData = ev.virality || {};

  /* stats */
  const factPct = summary.fact.pass_pct;
  const quarantined = (guardrail_sources.injections_blocked || []).length;
  const citeErrs = (guardrail_outputs.citation_errors || []).length;
  const rej = Object.entries(summary.editor.reject_pct_by_round)
    .map(([r, v]) => `v${r.slice(1)}:${Math.round(v)}%`).join("  ");
  $("#stats").innerHTML = `
    <div class="stat"><div class="k">Claims verified</div>
      <div class="v ${factPct >= 95 ? "good" : factPct === null ? "warn" : ""}">${
        factPct === null ? "—" : factPct + "%"}</div>
      <div class="n">${summary.fact.claims_checked} checked · ${summary.fact.dropped} dropped · ${summary.fact.retries} retries</div></div>
    <div class="stat"><div class="k">Editor rejects</div><div class="v" style="font-size:15px; padding-top:5px">${rej || "—"}</div>
      <div class="n">${summary.editor.rounds} round(s), max 3</div></div>
    <div class="stat"><div class="k">Guardrails</div>
      <div class="v ${citeErrs ? "warn" : "good"}">${citeErrs ? "flagged" : "clean"}</div>
      <div class="n">${quarantined} sources quarantined · ${summary.guardrail.pii_redactions} PII redacted</div></div>
    <div class="stat"><div class="k">Run</div><div class="v">${(summary.latency_ms / 1000).toFixed(1)}s</div>
      <div class="n">${summary.tokens.toLocaleString()} tokens · ${Math.max(0, ...Object.values(summary.versions))} drafts/platform</div></div>
    <div class="stat"><div class="k">Viral potential</div><div class="v ${summary.virality?.average >= 70 ? "good" : "warn"}">${summary.virality?.average ?? "—"}<small>/100</small></div>
      <div class="n">evidence-aware readiness score</div></div>`;

  /* fit meters in switcher */
  const liPost = splitLinkedIn(outputs.linkedin || "");
  const liWords = countWords(liPost);
  setMeter("li", `${liWords} / 600 words`, liWords / 600);
  const tweets = parseTweets(outputs.twitter || "");
  const worstTw = Math.max(0, ...tweets.map((t) => t.length));
  setMeter("tw", tweets.length ? `${tweets.length} tweets · worst ${worstTw}/280` : "—", worstTw / 280);
  const ig = splitInstagram(outputs.instagram || "");
  const igWords = countWords(ig.caption);
  const igSlides = ig.caption ? buildInstagramSlides(ig.caption, topicUsed) : [];
  setMeter("ig", ig.caption ? `${igWords} / 150 words · ${ig.tags.length}/20 tags · ${igSlides.length}-slide carousel` : "—", igWords / 150);

  renderLinkedIn(liPost, sources || []);
  renderTwitter(tweets);
  renderInstagram(ig, topicUsed);

  /* audit drawer data */
  $("#out-sources").innerHTML = (sources || []).map((s, i) =>
    `<li><span class="n">[${i + 1}]</span><span><span class="t">${esc(s.title)}</span>
      <span class="u">${esc(s.url)}</span></span><span class="d">${esc(s.date || "n.d.")}</span></li>`).join("")
    || `<p class="empty-note">No sources.</p>`;

  $("#out-facts").innerHTML = (claim_results || []).map((r) =>
    `<div class="fact-item">
      <span class="badge ${r.status === "pass" ? "pass" : r.status === "dropped" ? "drop" : "fail"}">${r.status}</span>
      <span class="score">[${r.citation}] ${r.score.toFixed(2)}</span>
      <span class="claim">${esc(r.claim)}<span class="why">${esc(r.reason)}</span></span>
    </div>`).join("") || `<p class="empty-note">No claims were checked.</p>`;

  const entries = Object.entries(editor_critiques || {})
    .flatMap(([p, cs]) => cs.map((c) => ({ ...c, platform: p })))
    .sort((a, b) => a.round - b.round);
  $("#out-editor").innerHTML = entries.map((c) =>
    `<div class="timeline-item">
      <span class="who">round ${c.round} · ${esc(c.platform)}</span>
      <span><span class="scores"><b>${c.clarity}</b> clarity · <b>${c.tone}</b> tone · <b>${c.platform_fit}</b> fit
        · <span class="verdict ${c.verdict === "approve" ? "ok" : "no"}">${c.verdict}</span></span>
        <span class="note">${esc(c.critique)}</span></span>
    </div>`).join("") || `<p class="empty-note">Editor loop did not run.</p>`;

  $("#out-log").textContent = (log || []).join("\n");
  $("#btn-audit").disabled = false;

  /* default to first selected platform */
  const first = $$(".pick input:checked")[0]?.value || "linkedin";
  switchPlatform(first);
}

function setMeter(key, label, ratio) {
  $(`#fit-${key}`).textContent = label;
  const pctv = Math.min(100, Math.max(3, Math.round(ratio * 100)));
  const bar = $(`#bar-${key} i`);
  bar.style.width = pctv + "%";
  bar.classList.toggle("over", ratio > 1);
}

/* ═══════════ platform switcher ═══════════ */
$("#switcher").addEventListener("click", (e) => {
  const b = e.target.closest(".sw");
  if (b) switchPlatform(b.dataset.platform);
});

function switchPlatform(p) {
  activePlatform = p;
  $$(".sw").forEach((b) => b.classList.toggle("active", b.dataset.platform === p));
  $$(".preview").forEach((el) => el.classList.toggle("active", el.dataset.platform === p));
  $(".canvas-glow").dataset.glow = p;
  renderVirality(p);
}

function renderVirality(platform) {
  const panel = $("#virality-lab");
  const report = viralityData[platform];
  if (!panel || !report) {
    if (panel) panel.innerHTML = "";
    return;
  }
  const dims = Object.entries(report.dimensions || {}).map(([key, value]) =>
    `<div class="viral-dim"><span>${esc(key.replaceAll("_", " "))}</span><b>${value}/5</b><i><em style="width:${value / 5 * 100}%"></em></i></div>`).join("");
  const recs = (report.recommendations || []).map((r) => `<li>${esc(r)}</li>`).join("");
  panel.innerHTML = `
    <div class="viral-head"><div><span class="eyebrow">VIRAL POTENTIAL · ${esc(platform)}</span><b>${report.score}/100</b><span class="viral-label">${esc(report.label)}</span></div><span class="viral-note">heuristic · not a reach guarantee</span></div>
    <div class="viral-grid"><div class="viral-dims">${dims}</div><div class="viral-angle"><span class="eyebrow">Suggested angle</span><p>${esc(report.angle)}</p><span class="eyebrow">Improve next</span><ul>${recs}</ul></div></div>
    <div class="viral-guardrail">✓ ${esc(report.guardrail)}</div>`;
}

/* ═══════════ LinkedIn native card ═══════════ */
function splitLinkedIn(raw) {
  const cut = raw.indexOf("\nSources:");
  return (cut >= 0 ? raw.slice(0, cut) : raw).trim();
}
function countWords(t) { return t ? t.trim().split(/\s+/).length : 0; }
function linkCites(text) {
  return esc(text).replace(/\[(\d+)\]/g, '<span class="cite">[$1]</span>');
}

function renderLinkedIn(post, sources) {
  const initials = "TF";
  const srcRows = sources.slice(0, 4).map((s, i) =>
    `<div>[${i + 1}] ${esc(s.title)} — <span style="color:#0a66c2">${esc(shortUrl(s.url))}</span></div>`).join("");
  const more = sources.length > 4 ? `<div style="color:#0a66c2">… ${sources.length - 4} more in Audit → Sources</div>` : "";
  $("#li-card").innerHTML = `
    <div class="li-head">
      <div class="li-ava">${initials}</div>
      <div class="li-id">
        <b>ThreadForge Studio</b>
        <span>AI content pipeline · Research-backed posts</span>
        <span class="li-time">Just now · 🌐</span>
      </div>
      <span class="li-follow">+ Follow</span>
    </div>
    <div class="li-body collapsed-text">${linkCites(post)}</div>
    <span class="li-more">…see more</span>
    <div class="li-srcs"><b>Sources cited in this post:</b>${srcRows}${more}</div>
    <div class="li-social">
      <span><svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.5 9h-4V5.7c0-.9-.2-1.6-.5-2-.4-.5-1-.7-1.9-.7-.4 1-.9 2.1-1.5 3.2-.6 1-1.5 2-2.6 2.8v8c1.6 1 3.6 1.5 5.9 1.5h2.6c1.1 0 1.8-.5 2.1-1.4l1.7-5.9c.3-1 .1-1.9-.5-2.5-.4-.5-1.1-.7-2.3-.7zM6.5 9H3.4c-.5 0-.9.4-.9.9v9.7c0 .5.4.9.9.9h3.1V9z"/></svg>Like</span>
      <span><svg viewBox="0 0 24 24" fill="currentColor"><path d="M7.4 9.5h5.9c-.4 1.2-1.4 2.4-2.9 3.4-1.4 1-3.2 1.6-4.9 1.6v3c2.9 0 5.6-1 7.7-2.4 2-1.5 3.4-3.4 3.7-5.6h4.7L15 3 7.4 9.5zM16.6 14.5h-5.9c.4-1.2 1.4-2.4 2.9-3.4 1.4-1 3.2-1.6 4.9-1.6v-3c-2.9 0-5.6 1-7.7 2.4-2 1.5-3.4 3.4-3.7 5.6H2.4L9 21l7.6-6.5z"/></svg>Repost</span>
      <span><svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 9h10a1 1 0 0 1 1 1v4.5a1 1 0 0 1-1 1h-2.6L11 19v-3.5H7a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1zm5-6C6.5 3 2 6.6 2 11c0 2.2 1.1 4.1 3 5.5V21l3.4-2.1c1.1.3 2.3.5 3.6.5 5.5 0 10-3.6 10-8s-4.5-8-10-8z"/></svg>Comment</span>
      <span><svg viewBox="0 0 24 24" fill="currentColor"><path d="M21.6 11.2l-8.9-8.9c-.3-.3-.7-.4-1.1-.2L4 4.6c-.4.2-.7.6-.7 1.1L3 13.4c0 .4.1.8.4 1l8.9 8.9c.3.3.8.3 1.1 0l7.8-7.8c.3-.3.3-.8 0-1.1zM8.6 9.7c-.9 0-1.6-.7-1.6-1.6s.7-1.6 1.6-1.6 1.6.7 1.6 1.6-.7 1.6-1.6 1.6z"/></svg>Send</span>
    </div>`;
  const body = $("#li-card .li-body");
  const moreBtn = $("#li-card .li-more");
  const applyClamp = () => {
    const clamped = body.classList.toggle("li-collapsed");
    moreBtn.textContent = clamped ? "…see more" : "see less";
    moreBtn.style.display = body.scrollHeight > body.clientHeight + 30 || !clamped ? "inline-block" : "none";
  };
  body.classList.add("li-collapsed");
  moreBtn.textContent = "…see more";
  moreBtn.onclick = () => {
    body.classList.toggle("li-collapsed");
    moreBtn.textContent = body.classList.contains("li-collapsed") ? "…see more" : "see less";
  };
  requestAnimationFrame(() => { moreBtn.style.display = body.scrollHeight > body.clientHeight + 30 ? "inline-block" : "none"; });
}

/* ═══════════ X thread native ═══════════ */
function parseTweets(raw) {
  return raw.split("\n\n").map((t) => t.trim()).filter(Boolean)
    .map((t) => t.replace(/^\d+\/\d+\s+/, ""));
}
const TWEET_LEN = (t) => t.replace(/https?:\/\/\S+/g, "x".repeat(23)).length;

function renderTwitter(tweets) {
  const icons = {
    reply: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M1.75 12.5c0-4.7 4.4-8.5 9.9-8.5 5 0 9.1 3.1 9.8 7.2.1.4.1.9.1 1.3 0 .4 0 .9-.1 1.3-.7 4.1-4.8 7.2-9.8 7.2-1.1 0-2.2-.2-3.2-.5l-4.6 2.3c-.3.2-.7 0-.8-.4l.9-3.9C2.1 16.8 1.75 14.8 1.75 12.5z"/></svg>`,
    rt: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4.5 3.9l2.7 2.7H14a4 4 0 0 1 4 4v2.2h-2V10.6a2 2 0 0 0-2-2H7.2l2.7 2.7-1.4 1.4L3.1 7.3l1.4-1.4zM19.5 20.1l-2.7-2.7H10a4 4 0 0 1-4-4v-2.2h2v2.2a2 2 0 0 0 2 2h6.8l-2.7-2.7 1.4-1.4 5.4 5.4-1.4 1.4z"/></svg>`,
    heart: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 20.8l-.9-.8C5.7 15.2 2.5 12.3 2.5 8.7c0-2.9 2.3-5.2 5.2-5.2 1.6 0 3.2.8 4.3 2 1.1-1.2 2.7-2 4.3-2 2.9 0 5.2 2.3 5.2 5.2 0 3.6-3.2 6.5-8.6 11.3l-.9.8z"/></svg>`,
    views: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 12h3l2.5-6 3 12 3-9 2 3h4v2h-5l-1.5-2-2.5 7h-2l-3-11-1.5 4H3v-2z"/></svg>`,
  };
  $("#tw-card").innerHTML = tweets.length ? `
    <div class="tw-thread-head">
      <span class="tw-thread-label">X THREAD</span>
      <span>${tweets.length} posts · ready to publish</span>
    </div>` : "";
  $("#tw-card").innerHTML += tweets.map((t, i) => {
    const len = TWEET_LEN(t);
    const ratio = Math.min(1, len / 280);
    const C = 2 * Math.PI * 10;
    const cls = len > 280 ? "over" : len > 250 ? "warn" : "";
    return `
    <div class="tw-tweet">
      ${i < tweets.length - 1 ? '<div class="tw-line"></div>' : ""}
      <div class="tw-head">
        <div class="tw-ava">TF</div>
        <div class="tw-name"><b>ThreadForge</b> <span>@threadforge · now</span><small>Post ${i + 1} of ${tweets.length}</small></div>
        <span class="tw-ring" title="${len}/280">
          <svg viewBox="0 0 24 24"><circle class="bg" cx="12" cy="12" r="10" fill="none" stroke-width="2"/>
          <circle class="fg ${cls}" cx="12" cy="12" r="10" fill="none" stroke-width="2"
            stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${(C * (1 - ratio)).toFixed(1)}"/></svg>
        </span>
      </div>
      <div class="tw-body">${linkCites(t)}</div>
      <div class="tw-actions"><span>${icons.reply}</span><span>${icons.rt}</span><span>${icons.heart}</span><span>${icons.views}</span></div>
    </div>`;
  }).join("") || `<p class="empty-note">X thread not selected.</p>`;
}

/* ═══════════ Instagram native ═══════════ */
function splitInstagram(raw) {
  if (!raw) return { caption: "", tags: [] };
  const blocks = raw.trim().split("\n\n");
  let tags = [];
  if (blocks.length && blocks[blocks.length - 1].startsWith("#")) {
    tags = blocks.pop().split(/\s+/).filter(Boolean);
  }
  return { caption: blocks.join("\n\n").trim(), tags };
}
function shortUrl(u) { return u.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""); }

function buildInstagramSlides(caption, topic) {
  const sentences = caption.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [caption];
  const clean = sentences.map((s) => s.trim()).filter(Boolean);
  const slides = [];
  const title = topic.trim().split(/\s+/).slice(0, 7).join(" ");
  const takeawayTitle = (body) => {
    const words = body.replace(/\[\d+\]/g, "").replace(/[.!?]+$/, "").trim().split(/\s+/);
    return words.slice(0, 6).join(" ") + (words.length > 6 ? "…" : "");
  };
  if (clean.length) slides.push({
    kicker: "THE QUICK TAKE",
    title,
    body: clean.shift(),
  });
  for (let i = 0; i < clean.length && slides.length < 5; i += 2) {
    slides.push({
      kicker: `WHAT THE SOURCES SAY · ${slides.length}`,
      title: takeawayTitle(clean[i]),
      body: clean.slice(i, i + 2).join(" "),
    });
  }
  return slides.length ? slides : [{ kicker: "CITED TAKEAWAY", title, body: caption }];
}

function renderInstagram(ig, topic) {
  if (!ig.caption) {
    $("#ig-card").innerHTML = `<p class="empty-note" style="padding:20px">Instagram not selected.</p>`;
    return;
  }
  const gradients = [
    "linear-gradient(135deg,#4f5bd5,#962fbf,#d62976)",
    "linear-gradient(135deg,#0f2027,#203a43,#2c5364)",
    "linear-gradient(135deg,#f7971e,#ffd200)",
    "linear-gradient(135deg,#11998e,#38ef7d)",
    "linear-gradient(135deg,#fc466b,#3f5efb)",
  ];
  let h = 0;
  for (const ch of topic) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const grad = gradients[h % gradients.length];
  const slides = buildInstagramSlides(ig.caption, topic);
  const slideHtml = slides.map((slide, i) => `
    <div class="ig-slide ${i === 0 ? "active" : ""}" data-slide="${i}">
      <div class="ig-slide-top"><span class="ig-slide-mark">TF</span><span>RESEARCH-BACKED</span><span>${String(i + 1).padStart(2, "0")} / ${String(slides.length).padStart(2, "0")}</span></div>
      <div class="ig-slide-copy">
        <div class="ig-slide-kicker">${esc(slide.kicker)}</div>
        <div class="ig-slide-title">${esc(slide.title)}</div>
        <div class="ig-slide-body">${linkCites(slide.body)}</div>
      </div>
      <div class="ig-slide-foot"><span>@threadforge.studio</span><span>verify before you amplify</span></div>
    </div>`).join("");
  const dots = slides.map((_, i) => `<button class="ig-dot ${i === 0 ? "active" : ""}" data-slide="${i}" aria-label="Show Instagram slide ${i + 1}"></button>`).join("");

  $("#ig-card").innerHTML = `
    <div class="ig-card">
      <div class="ig-head">
        <div class="ig-ava"><i>TF</i></div>
        <span class="ig-username">threadforge.studio</span>
        <svg class="dots" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>
      </div>
      <div class="ig-media" style="background:${grad}">
        <div class="ig-carousel">${slideHtml}</div>
        ${slides.length > 1 ? `<button class="ig-nav prev" aria-label="Previous Instagram slide">‹</button><button class="ig-nav next" aria-label="Next Instagram slide">›</button><div class="ig-dots">${dots}</div>` : ""}
      </div>
      <div class="ig-actions">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20.8l-.9-.8C5.7 15.2 2.5 12.3 2.5 8.7c0-2.9 2.3-5.2 5.2-5.2 1.6 0 3.2.8 4.3 2 1.1-1.2 2.7-2 4.3-2 2.9 0 5.2 2.3 5.2 5.2 0 3.6-3.2 6.5-8.6 11.3l-.9.8z"/></svg>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.4c-1.5 0-3-.4-4.2-1L3 20l1.2-5.1a8.3 8.3 0 0 1-1.2-4.4A8.4 8.4 0 0 1 11.5 2.1 8.4 8.4 0 0 1 21 11.5z"/></svg>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 3L9.2 12.7M22 3l-7 19-3.8-8.3L3 10l19-7z"/></svg>
        <svg class="ig-save" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>
      </div>
      <div class="ig-likes">Liked by fact.checker and ${ig.tags.length} others</div>
      <div class="ig-caption ig-caption-collapsed">
        <span class="ig-user">threadforge.studio</span><span class="ig-cap-text">${linkCites(ig.caption)}</span>
        ${ig.tags.length ? `<span class="tags"> ${esc(ig.tags.join(" "))}</span>` : ""}
      </div>
      <span class="ig-more">more</span>
      <div class="ig-time">Just now</div>
    </div>`;
  const cap = $("#ig-card .ig-caption");
  const more = $("#ig-card .ig-more");
  more.onclick = () => {
    cap.classList.toggle("ig-caption-collapsed");
    more.textContent = cap.classList.contains("ig-caption-collapsed") ? "more" : "less";
  };
  const slideEls = $$("#ig-card .ig-slide");
  const dotEls = $$("#ig-card .ig-dot");
  let activeSlide = 0;
  const showSlide = (index) => {
    activeSlide = (index + slideEls.length) % slideEls.length;
    slideEls.forEach((el, i) => el.classList.toggle("active", i === activeSlide));
    dotEls.forEach((el, i) => el.classList.toggle("active", i === activeSlide));
  };
  $("#ig-card .ig-nav.prev")?.addEventListener("click", () => showSlide(activeSlide - 1));
  $("#ig-card .ig-nav.next")?.addEventListener("click", () => showSlide(activeSlide + 1));
  dotEls.forEach((dot) => dot.addEventListener("click", () => showSlide(Number(dot.dataset.slide))));
  requestAnimationFrame(() => { more.style.display = cap.scrollHeight > cap.clientHeight + 8 ? "inline-block" : "none"; });
}

/* ═══════════ copy ═══════════ */
$("#btn-copy-active").addEventListener("click", async (e) => {
  if (!lastResult) return;
  const text = lastResult.outputs[activePlatform] || "";
  await navigator.clipboard.writeText(text);
  const btn = e.currentTarget;
  const old = btn.innerHTML;
  btn.innerHTML = "✓ Copied";
  setTimeout(() => (btn.innerHTML = old), 1400);
});

fillForm();
probeHealth();
buildRail();

/* restore the last run after a refresh (audit trail survives page reloads) */
try {
  const saved = JSON.parse(localStorage.getItem("itp_last_run") || "null");
  if (saved?.ev?.outputs && Object.keys(saved.ev.outputs).length) {
    topicUsed = saved.topic || "";
    if (!$("#topic").value) $("#topic").value = topicUsed;
    lastResult = saved.ev;
    renderWorkspace(saved.ev);
    $("#results").classList.remove("hidden");
  }
} catch {}
