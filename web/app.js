// SignalHunt dashboard: vanilla JS, hash routing, polling. No build step.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
};
const toast = (msg, ok = true) => {
  const t = $("#toast"); t.textContent = msg; t.className = `fixed bottom-5 right-5 card px-4 py-3 shadow-lg text-sm ${ok ? "" : "border-red-300 text-red-700"}`;
  setTimeout(() => t.classList.add("hidden"), 3500);
};
const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "-");
const usd = (x) => `$${Number(x || 0).toFixed(4)}`;
let timer = null;
const poll = (fn, ms = 2000) => { clearInterval(timer); timer = setInterval(fn, ms); };

const PRI = { HIGH: "bg-rose-100 text-rose-700", MEDIUM: "bg-amber-100 text-amber-700", LOW: "bg-slate-100 text-slate-600" };
const CONF = { HIGH: "bg-emerald-100 text-emerald-700", MEDIUM: "bg-sky-100 text-sky-700", LOW: "bg-slate-100 text-slate-600" };
const STAT = {
  VERIFIED: "bg-emerald-100 text-emerald-700", CANNOT_VERIFY: "bg-amber-100 text-amber-800", REJECTED: "bg-slate-200 text-slate-600",
  DUPLICATE: "bg-violet-100 text-violet-700", COMPLETED: "bg-emerald-100 text-emerald-700", RUNNING: "bg-indigo-100 text-indigo-700",
  QUEUED: "bg-slate-100 text-slate-600", FAILED: "bg-red-100 text-red-700", STOPPED_LIMIT: "bg-amber-100 text-amber-800",
  PENDING_APPROVAL: "bg-amber-100 text-amber-800", APPROVED: "bg-sky-100 text-sky-700", SENDING: "bg-indigo-100 text-indigo-700",
  SENT: "bg-emerald-100 text-emerald-700", RESOLVED: "bg-emerald-100 text-emerald-700", AMBIGUOUS: "bg-amber-100 text-amber-800",
  NOT_FOUND: "bg-amber-100 text-amber-800", UNRESOLVED_NAME_ONLY: "bg-slate-100 text-slate-600",
};
const pill = (v, map = STAT) => `<span class="pill ${map[v] || "bg-slate-100 text-slate-600"}">${esc(String(v).replaceAll("_", " "))}</span>`;
const kpi = (label, value, sub = "") => `<div class="card p-5"><div class="text-xs font-medium text-slate-500 uppercase tracking-wide">${label}</div><div class="text-3xl font-bold mt-1">${value}</div><div class="text-xs text-slate-500 mt-1">${sub}</div></div>`;

// ------------------------------------------------------------ routes
const routes = {
  "": dashboard, discover, opportunities, opp: oppDetail, outreach, activity, run: runView,
};
async function router() {
  clearInterval(timer);
  const [_, page = "", arg] = location.hash.split("/");
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active",
    (a.dataset.r === "dash" && page === "") || a.dataset.r === page || (a.dataset.r === "opps" && (page === "opportunities" || page === "opp")) || (a.dataset.r === "discover" && page === "run")));
  try { await (routes[page] || dashboard)(arg); } catch (e) { $("#view").innerHTML = `<div class="card p-6 text-red-700">${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", router);

async function sidebar() {
  try {
    const h = await api("/api/health");
    $("#sysStatus").innerHTML = `
      <div class="font-semibold text-slate-700 mb-1">${esc(h.client)}</div>
      <div>Model: ${h.llm ? `<span class="text-emerald-600">${esc(h.model)}</span>` : '<span class="text-amber-600">rule-based fallback</span>'}</div>
      <div>Crustdata: ${h.crustdata ? '<span class="text-emerald-600">connected</span>' : '<span class="text-amber-600">not configured</span>'}</div>
      <div>Outreach: ${esc(h.outreach_mode)}</div>
      <div>Limits: $${h.limits.max_cost_usd} / ${h.limits.max_seconds}s per run</div>`;
    const s = await api("/api/stats");
    $("#pendingBadge").innerHTML = s.pending_approval ? `<span class="pill bg-amber-100 text-amber-800">${s.pending_approval}</span>` : "";
  } catch (_) {}
}

// ------------------------------------------------------------ dashboard
async function dashboard() {
  const draw = async () => {
    const [s, runs, opps] = await Promise.all([api("/api/stats"), api("/api/runs?limit=6"), api("/api/opportunities")]);
    $("#view").innerHTML = `
      <div class="flex flex-wrap items-end justify-between gap-3 mb-6">
        <div><h1 class="text-2xl font-bold">Dashboard</h1><p class="text-slate-500 text-sm">Companies showing verified buying triggers for 3PL services</p></div>
        <a href="#/discover" class="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700">+ New discovery</a>
      </div>
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        ${kpi("Companies", s.companies, `${s.runs} runs`)}
        ${kpi("Verified triggers", s.verified_triggers, `${s.cannot_verify} cannot verify · ${s.rejected} rejected`)}
        ${kpi("Qualified opportunities", s.opportunities, `${s.high_priority} high priority`)}
        ${kpi("Outreach", s.sent + " sent", `${s.pending_approval} awaiting approval · spend ${usd(s.total_cost_usd)} (₹${s.total_cost_inr})`)}
      </div>
      <div class="grid lg:grid-cols-3 gap-6">
        <div class="card lg:col-span-2">
          <div class="px-5 py-4 border-b font-semibold">Top opportunities</div>
          ${opps.length ? `<table class="w-full text-sm"><tbody>${opps.slice(0, 8).map(oppRow).join("")}</tbody></table>` : `<div class="p-8 text-center text-slate-500 text-sm">No opportunities yet. Start a discovery.</div>`}
        </div>
        <div class="card">
          <div class="px-5 py-4 border-b font-semibold">Recent runs</div>
          <div class="divide-y">${runs.map((r) => `
            <a href="#/run/${r.id}" class="block px-5 py-3 hover:bg-slate-50">
              <div class="flex justify-between gap-2">${pill(r.status)}<span class="text-xs text-slate-400">${fmtTime(r.created_at)}</span></div>
              <div class="text-sm mt-1 line-clamp-2">${esc(r.request_text)}</div>
              <div class="text-xs text-slate-500 mt-1">${esc(r.progress.phase || "")} · ${r.progress.opportunities ?? 0} opps · ${usd(r.progress.cost_usd)}${r.attempts > 1 ? ` · resumed ×${r.attempts - 1}` : ""}</div>
            </a>`).join("") || '<div class="p-5 text-sm text-slate-500">No runs yet.</div>'}</div>
        </div>
      </div>`;
  };
  await draw(); sidebar(); poll(draw, 4000);
}

function oppRow(o) {
  return `<tr class="border-b last:border-0 hover:bg-slate-50 cursor-pointer" onclick="location.hash='#/opp/${o.id}'">
    <td><div class="font-semibold">${esc(o.company_name)}</div><div class="text-xs text-slate-500">${esc(o.industry)} · ${esc((o.cities || []).join(", ") || o.state)}</div></td>
    <td class="text-sm">${esc(o.event_label)}<div class="text-xs text-slate-500">${esc(o.when?.latest_record_date || "")}</div></td>
    <td>${pill(o.priority, PRI)}</td>
    <td class="text-right font-bold text-lg">${o.score}</td></tr>`;
}

// ------------------------------------------------------------ discover
const EXAMPLES = [
  "Find companies in Gujarat that have announced warehouse expansion or manufacturing expansion in the last 30 days and could potentially require third-party logistics services.",
  "Find manufacturing and e-commerce companies in Gujarat and Rajasthan that have announced expansion, new facilities or warehouse-related developments in the last 30 days.",
  "Find companies opening new plants or distribution centres in Maharashtra or Karnataka in the last 2 weeks.",
];
async function discover() {
  const runs = await api("/api/runs?limit=10");
  $("#view").innerHTML = `
    <h1 class="text-2xl font-bold mb-1">Discover opportunities</h1>
    <p class="text-slate-500 text-sm mb-6">Describe who you want to find. SignalHunt searches live news, verifies each fact against its source, and qualifies the leads.</p>
    <div class="card p-5 mb-6">
      <label class="text-sm font-semibold">Targeting request</label>
      <textarea id="req" rows="4" class="w-full mt-2 border rounded-lg p-3 text-sm focus:outline-indigo-500">${esc(EXAMPLES[0])}</textarea>
      <div class="flex flex-wrap gap-2 mt-2">${EXAMPLES.map((e, i) => `<button class="text-xs px-2 py-1 rounded bg-slate-100 hover:bg-slate-200" onclick="document.getElementById('req').value=EXAMPLES[${i}]">Example ${i + 1}</button>`).join("")}</div>
      <div class="flex justify-end mt-4"><button id="go" class="px-5 py-2 rounded-lg bg-indigo-600 text-white font-semibold text-sm hover:bg-indigo-700">Start discovery</button></div>
    </div>
    <div class="card"><div class="px-5 py-4 border-b font-semibold">Runs</div>
      <table class="w-full text-sm"><tbody>${runs.map((r) => `<tr class="border-b last:border-0 hover:bg-slate-50 cursor-pointer" onclick="location.hash='#/run/${r.id}'">
        <td class="w-36">${pill(r.status)}</td><td>${esc(r.request_text).slice(0, 140)}</td><td class="text-xs text-slate-500 whitespace-nowrap">${fmtTime(r.created_at)}</td></tr>`).join("")}</tbody></table></div>`;
  $("#go").onclick = async () => {
    const request = $("#req").value.trim();
    try { const r = await api("/api/runs", { method: "POST", body: JSON.stringify({ request }) }); location.hash = `#/run/${r.run_id}`; }
    catch (e) { toast(e.message, false); }
  };
}

// ------------------------------------------------------------ run view (live progress + results)
async function runView(id) {
  const draw = async () => {
    const r = await api(`/api/runs/${id}`);
    const p = r.progress || {};
    const live = ["RUNNING", "QUEUED"].includes(r.status);
    const pct = p.articles_total ? Math.round((100 * (p.articles_done || 0)) / p.articles_total) : (r.status === "COMPLETED" ? 100 : 5);
    const spec = r.spec;
    $("#view").innerHTML = `
      <a href="#/discover" class="text-sm text-indigo-600">← Runs</a>
      <div class="flex flex-wrap items-center gap-3 mt-2 mb-1"><h1 class="text-xl font-bold">Run ${esc(id)}</h1>${pill(r.status)}
        ${live ? '<svg class="w-4 h-4 spin text-indigo-600" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity=".25"/><path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="3"/></svg>' : ""}
        ${["FAILED", "STOPPED_LIMIT"].includes(r.status) ? `<button class="px-3 py-1 text-sm rounded bg-slate-800 text-white" onclick="retryRun('${id}')">Resume run</button>` : ""}</div>
      <p class="text-sm text-slate-600 mb-4">${esc(r.request)}</p>
      ${r.error ? `<div class="card p-3 mb-4 text-sm text-amber-800 bg-amber-50">${esc(r.error)}</div>` : ""}
      <div class="card p-5 mb-6">
        <div class="flex justify-between text-sm mb-2"><span class="font-semibold">${esc(p.phase || "queued")}</span><span class="text-slate-500">${p.articles_done || 0}/${p.articles_total || "?"} sources · ${p.active_seconds || 0}s · ${usd(p.cost_usd)}${p.recovered ? ` · <b class="text-indigo-700">recovered after restart ×${p.recovered}</b> (${p.cache_hits} steps replayed from cache)` : ""}</span></div>
        <div class="bar"><div style="width:${pct}%"></div></div>
        <div class="grid grid-cols-2 md:grid-cols-6 gap-3 mt-4 text-center text-sm">
          ${[["Queries", p.queries], ["Duplicate listings", p.duplicate_listings_removed], ["Verified", p.verified], ["Cannot verify", p.cannot_verify], ["Rejected", p.rejected], ["Opportunities", p.opportunities]].map(([k, v]) => `<div class="bg-slate-50 rounded-lg py-2"><div class="font-bold text-lg">${v ?? 0}</div><div class="text-xs text-slate-500">${k}</div></div>`).join("")}
        </div>
        ${spec ? `<div class="mt-4 text-xs text-slate-600 flex flex-wrap gap-2">
          <span class="pill bg-slate-100">States: ${esc(spec.states.join(", ") || "India-wide")}</span>
          <span class="pill bg-slate-100">Window: ${spec.window_days} days (${esc(spec.window_start)} → ${esc(spec.window_end)})</span>
          <span class="pill bg-slate-100">Triggers: ${esc(spec.triggers.map((t) => t.replaceAll("_", " ").toLowerCase()).join(", "))}</span>
          ${spec.industries.length ? `<span class="pill bg-slate-100">Industries: ${esc(spec.industries.join(", "))}</span>` : ""}
          <span class="pill bg-slate-100">Parsed by: ${esc(spec.parse_method)}</span></div>` : ""}
      </div>
      <h2 class="font-semibold mb-3">Qualified opportunities (${r.opportunities.length})</h2>
      <div class="card mb-6">${r.opportunities.length ? `<table class="w-full text-sm"><tbody>${r.opportunities.map(oppRow).join("")}</tbody></table>` : `<div class="p-6 text-sm text-slate-500">${live ? "Working…" : "No verified opportunities for this request."}</div>`}</div>
      <div class="grid md:grid-cols-2 gap-6">
        ${candList("Cannot verify", r.cannot_verify, "What was missing is named for each item.")}
        ${candList("Rejected", r.rejected, "Outdated, out of scope, rivals, or no qualifying event.")}
      </div>
      ${r.duplicates.length ? `<div class="mt-6">${candList("Duplicate reports merged", r.duplicates, "Same underlying event reported by another outlet; kept as corroborating evidence.")}</div>` : ""}
      <div class="card p-5 mt-6 text-sm"><div class="font-semibold mb-2">Cost of this run: ${usd(r.cost.total_usd)} (₹${r.cost.total_inr})</div>
        <table class="w-full text-xs"><thead class="text-slate-500 text-left"><tr><th>Provider</th><th>Calls</th><th>OK</th><th>Failed/retried</th><th class="text-right">Cost</th></tr></thead>
        <tbody>${r.cost.by_provider.map((x) => `<tr class="border-t"><td>${esc(x.provider)}</td><td>${x.calls}</td><td>${x.ok}</td><td>${x.failed}</td><td class="text-right">${usd(x.cost)}</td></tr>`).join("")}</tbody></table></div>`;
    if (!live) clearInterval(timer);
  };
  await draw(); poll(draw, 2000);
}
window.retryRun = async (id) => { await api(`/api/runs/${id}/retry`, { method: "POST" }); toast("Run resumed"); };

function candList(title, items, sub) {
  return `<div class="card"><div class="px-5 py-3 border-b"><div class="font-semibold">${title} (${items.length})</div><div class="text-xs text-slate-500">${sub}</div></div>
    <div class="divide-y max-h-96 overflow-auto">${items.map((c) => `<div class="px-5 py-3 text-sm">
      <div class="flex gap-2 items-start">${pill(c.status)}<span class="font-medium">${esc(c.company || c.source_title || "Untitled source")}</span></div>
      <div class="text-xs text-slate-600 mt-1">${esc(c.reason)}</div>
      ${c.source_url ? `<a target="_blank" rel="noopener" href="${esc(c.source_url)}" class="text-xs text-indigo-600 break-all">${esc(c.source_url).slice(0, 110)}</a>` : ""}
    </div>`).join("") || '<div class="px-5 py-3 text-sm text-slate-400">None</div>'}</div></div>`;
}

// ------------------------------------------------------------ opportunities explorer
async function opportunities() {
  const opps = await api("/api/opportunities");
  const draw = (f = "") => {
    const list = opps.filter((o) => !f || JSON.stringify([o.company_name, o.state, o.event_label, o.industry]).toLowerCase().includes(f));
    $("#rows").innerHTML = list.map((o) => `<tr class="border-b hover:bg-slate-50 cursor-pointer" onclick="location.hash='#/opp/${o.id}'">
      <td class="font-semibold">${esc(o.company_name)}</td><td>${esc(o.industry)}</td><td>${esc((o.cities || []).join(", ") || o.state)}</td>
      <td>${esc(o.event_label)}</td><td class="whitespace-nowrap">${esc(o.when?.latest_record_date || "")}</td>
      <td class="font-bold">${o.score}</td><td>${pill(o.priority, PRI)}</td><td>${pill(o.confidence, CONF)}</td><td>${pill(o.verification)}</td><td>${o.independent_sources.length}</td></tr>`).join("")
      || '<tr><td colspan="10" class="text-center text-slate-500 py-8">No opportunities.</td></tr>';
  };
  $("#view").innerHTML = `
    <div class="flex flex-wrap justify-between items-end gap-3 mb-4"><div><h1 class="text-2xl font-bold">Opportunity explorer</h1><p class="text-sm text-slate-500">${opps.length} qualified opportunities, ranked by transparent score</p></div>
    <input id="flt" placeholder="Filter by company, state, trigger…" class="border rounded-lg px-3 py-2 text-sm w-72"/></div>
    <div class="card overflow-x-auto"><table class="w-full text-sm"><thead class="text-left text-xs uppercase text-slate-500 border-b"><tr>
      <th>Company</th><th>Industry</th><th>Location</th><th>Trigger</th><th>Date</th><th>Score</th><th>Priority</th><th>Confidence</th><th>Status</th><th>Sources</th></tr></thead><tbody id="rows"></tbody></table></div>`;
  $("#flt").oninput = (e) => draw(e.target.value.toLowerCase());
  draw();
}

// ------------------------------------------------------------ opportunity detail
async function oppDetail(id) {
  const o = await api(`/api/opportunities/${id}`);
  const r = o.report || {};
  const dm = o.decision_makers || {};
  const idn = o.identity || {};
  const act = (o.actions || [])[0];
  $("#view").innerHTML = `
    <a href="#/opportunities" class="text-sm text-indigo-600">← Opportunities</a>
    <div class="flex flex-wrap items-start justify-between gap-4 mt-2 mb-6">
      <div><h1 class="text-2xl font-bold">${esc(o.company_name)}</h1>
        <div class="text-sm text-slate-500 mt-1">${esc(o.event_label)} · ${esc((o.cities || []).join(", ") || "")} ${esc(o.state)} · Industry: ${esc(o.industry)}</div>
        <div class="flex gap-2 mt-2">${pill(o.verification)} ${pill(o.priority, PRI)} <span class="pill bg-slate-100">Confidence ${esc(o.confidence)}</span> ${pill(idn.status)}</div></div>
      <div class="card px-6 py-4 text-center"><div class="text-xs text-slate-500 uppercase">Opportunity score</div><div class="text-4xl font-bold text-indigo-600">${o.score}</div><div class="text-xs text-slate-500">of 100</div></div>
    </div>
    <div class="grid lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 space-y-6">
        <div class="card p-5"><div class="flex justify-between"><h2 class="font-semibold">What happened</h2><span class="text-xs text-slate-500">Verified fact · from source</span></div>
          <div class="quote mt-3 text-sm">“${esc(o.headline_quote)}”</div>
          <div class="text-xs text-slate-500 mt-2">Latest record ${esc(o.when?.latest_record_date || "-")} · ${o.independent_sources.length} independent source(s) · ${o.duplicate_reports_merged} duplicate report(s) merged
            ${o.investment ? ` · Investment: <b>${esc(o.investment)}</b>` : ""}${o.scale ? ` · Scale: <b>${esc(o.scale)}</b>` : ""}</div></div>

        <div class="card p-5"><h2 class="font-semibold mb-3">Evidence (${o.evidence.length} sourced facts)</h2>
          <div class="space-y-3">${o.evidence.map((f) => `<div class="border rounded-lg p-3 text-sm ${f.conflicting ? "border-amber-300 bg-amber-50" : ""}">
            <div class="flex flex-wrap gap-2 items-center"><span class="pill bg-indigo-50 text-indigo-700">${esc(f.field)}</span><b>${esc(f.value)}</b>${f.conflicting ? pill("CANNOT_VERIFY") + '<span class="text-xs text-amber-700">sources conflict</span>' : '<span class="pill bg-emerald-50 text-emerald-700">✓ quote found verbatim</span>'}</div>
            <div class="quote mt-2 text-xs">“${esc(f.quote)}”</div>
            <div class="text-xs text-slate-500 mt-2">${esc(f.source_name)} · published ${esc(f.published || "?")} <span class="mono">(${esc(f.published_field || "")})</span> · retrieved ${esc(f.retrieved_at)} via ${esc(f.via)}<br/>
            <a target="_blank" rel="noopener" class="text-indigo-600 break-all" href="${esc(f.source_url)}">${esc(f.source_url)}</a></div></div>`).join("")}</div></div>

        <div class="card p-5"><div class="flex justify-between"><h2 class="font-semibold">Why this could indicate 3PL demand</h2><span class="pill bg-violet-100 text-violet-700">${r.method === "llm" ? "Model interpretation (Claude)" : "Template interpretation (no model)"} · inference, not fact</span></div>
          <p class="text-sm mt-3">${esc(r.why_3pl_demand)}</p>
          <h3 class="font-semibold text-sm mt-4">Potential logistics requirements <span class="text-xs font-normal text-slate-500">(inference)</span></h3>
          <ul class="list-disc ml-5 text-sm mt-1">${(r.logistics_requirements || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
          <h3 class="font-semibold text-sm mt-4">Recommended sales approach</h3><p class="text-sm mt-1">${esc(r.sales_approach)}</p></div>

        <div class="card p-5"><h2 class="font-semibold mb-3">Timeline</h2>
          <ol class="border-l-2 border-indigo-200 ml-2 space-y-3">${o.timeline.map((t) => `<li class="ml-4 text-sm"><div class="text-xs text-slate-500">${esc(t.date)} · ${esc(t.source)}</div><a class="font-medium hover:text-indigo-600" target="_blank" rel="noopener" href="${esc(t.url)}">${esc(t.title)}</a></li>`).join("")}</ol></div>
      </div>
      <div class="space-y-6">
        <div class="card p-5"><h2 class="font-semibold mb-3">Score breakdown</h2>
          ${o.scoring.breakdown.map((b) => `<div class="mb-3"><div class="flex justify-between text-sm"><span>${esc(b.component.replaceAll("_", " "))}</span><b>${b.points}/${b.weight}</b></div>
            <div class="bar mt-1"><div style="width:${(100 * b.points) / b.weight}%"></div></div><div class="text-xs text-slate-500 mt-1">${esc(b.reason)}</div></div>`).join("")}
          <details class="text-xs text-slate-500 mt-2"><summary class="cursor-pointer">Scoring assumptions</summary><ul class="list-disc ml-4 mt-1">${o.scoring.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details></div>

        <div class="card p-5"><h2 class="font-semibold mb-3">Decision-makers</h2>
          ${dm.status === "VERIFIED" ? dm.people.map((p) => `<div class="border rounded-lg p-3 mb-2 text-sm"><div class="font-semibold">${esc(p.name)}</div><div class="text-slate-600">${esc(p.title)}</div>
            <div class="text-xs text-slate-500 mt-1">Source: ${esc(p.evidence.source)} · ${esc(p.evidence.field)} · retrieved ${esc(p.evidence.retrieved_at)}</div>
            ${p.profile_url ? `<a target="_blank" rel="noopener" class="text-xs text-indigo-600" href="${esc(p.profile_url)}">Professional profile</a>` : ""}</div>`).join("")
            : `${pill("CANNOT_VERIFY")}<div class="text-sm text-slate-600 mt-2">${esc(dm.missing)}</div>`}
          <div class="text-xs text-slate-400 mt-2">Only name, current title and public profile are kept. No personal email or phone is collected.</div></div>

        <div class="card p-5"><h2 class="font-semibold mb-2">Company identity</h2>
          <div class="text-sm">${pill(idn.status)} <span class="text-slate-600">${esc(idn.note)}</span></div>
          ${idn.evidence ? `<div class="text-xs text-slate-500 mt-2">${esc(idn.evidence.source)} · field ${esc(idn.evidence.field)} = <b>${esc(idn.evidence.value)}</b> · id ${esc(idn.evidence.crustdata_company_id)} · ${esc(idn.evidence.domain || "")}</div>` : ""}
          ${(idn.lookalikes || []).length ? `<div class="text-xs mt-2"><b>Lookalikes not merged:</b> ${idn.lookalikes.map((l) => esc(l.name)).join(", ")}</div>` : ""}</div>

        ${o.unverified.length ? `<div class="card p-5"><h2 class="font-semibold mb-2">Not verified</h2><ul class="text-xs text-slate-600 list-disc ml-4">${o.unverified.map((u) => `<li>${esc(u)}</li>`).join("")}</ul></div>` : ""}
        ${act ? `<div class="card p-5"><h2 class="font-semibold mb-2">Outreach</h2>${pill(act.status)} <a href="#/outreach" class="text-sm text-indigo-600 ml-2">Review email →</a></div>` : ""}
      </div>
    </div>`;
}

// ------------------------------------------------------------ outreach / approvals
async function outreach() {
  const [acts, opps] = await Promise.all([api("/api/actions"), api("/api/opportunities")]);
  const byId = Object.fromEntries(opps.map((o) => [o.id, o]));
  $("#view").innerHTML = `
    <h1 class="text-2xl font-bold mb-1">Outreach</h1>
    <p class="text-sm text-slate-500 mb-6">Every email waits for a named person's approval. The approver types the recipient address: SignalHunt never guesses contact details. Each opportunity can be emailed once.</p>
    <div class="space-y-4">${acts.map((a) => {
      const o = byId[a.opp_id] || {}; const p = a.payload; const pend = a.status === "PENDING_APPROVAL";
      return `<div class="card p-5">
        <div class="flex flex-wrap justify-between gap-2"><div><a href="#/opp/${a.opp_id}" class="font-semibold hover:text-indigo-600">${esc(o.company_name || a.opp_id)}</a>
          <div class="text-xs text-slate-500">${esc(o.event_label || "")} · To: ${esc(p.recipient ? p.recipient.name + ", " + p.recipient.title : "decision-maker not verified")} ${p.to_email ? "&lt;" + esc(p.to_email) + "&gt;" : ""}</div></div>
          <div class="flex gap-2 items-center">${pill(a.status)}<span class="text-xs text-slate-400 mono">key ${esc(a.idempotency_key.slice(0, 10))}</span></div></div>
        <input id="s_${a.id}" ${pend ? "" : "disabled"} class="w-full border rounded-lg px-3 py-2 text-sm mt-3 font-medium disabled:bg-slate-50" value="${esc(p.subject)}"/>
        <textarea id="b_${a.id}" ${pend ? "" : "disabled"} rows="9" class="w-full border rounded-lg p-3 text-sm mt-2 disabled:bg-slate-50">${esc(p.body)}</textarea>
        ${pend ? `<div class="flex flex-wrap gap-2 mt-3 items-center">
          <input id="t_${a.id}" placeholder="recipient@company.com (typed by you)" class="border rounded-lg px-3 py-2 text-sm w-72"/>
          <input id="n_${a.id}" placeholder="Your name (approver)" class="border rounded-lg px-3 py-2 text-sm w-52"/>
          <button onclick="saveDraft('${a.id}')" class="px-3 py-2 text-sm rounded-lg border">Save edits</button>
          <button onclick="approveAct('${a.id}')" class="px-4 py-2 text-sm rounded-lg bg-emerald-600 text-white font-semibold">Approve & send</button>
          <button onclick="rejectAct('${a.id}')" class="px-3 py-2 text-sm rounded-lg text-red-600">Reject</button></div>`
        : `<div class="text-xs text-slate-500 mt-2">${a.approved_by ? `Decision by ${esc(a.approved_by)} · ${fmtTime(a.approved_at)}` : ""} ${a.sent_at ? ` · sent ${fmtTime(a.sent_at)} · ${esc(a.provider_ref)}` : ""} ${a.error ? " · " + esc(a.error) : ""}</div>`}
      </div>`; }).join("") || '<div class="card p-8 text-center text-slate-500 text-sm">No drafts yet.</div>'}</div>`;
  sidebar();
}
window.saveDraft = async (id) => {
  try { await api(`/api/actions/${id}`, { method: "PUT", body: JSON.stringify({ subject: $(`#s_${id}`).value, body: $(`#b_${id}`).value }) }); toast("Draft saved"); }
  catch (e) { toast(e.message, false); }
};
window.approveAct = async (id) => {
  const to = $(`#t_${id}`).value.trim(), by = $(`#n_${id}`).value.trim();
  if (!confirm(`Send this email to ${to}? This cannot be undone.`)) return;
  try { const a = await api(`/api/actions/${id}/approve`, { method: "POST", body: JSON.stringify({ approver: by, to_email: to, subject: $(`#s_${id}`).value, body: $(`#b_${id}`).value }) }); toast(`Status: ${a.status}`); outreach(); }
  catch (e) { toast(e.message, false); }
};
window.rejectAct = async (id) => {
  const by = $(`#n_${id}`).value.trim() || "reviewer";
  try { await api(`/api/actions/${id}/reject`, { method: "POST", body: JSON.stringify({ approver: by, reason: "rejected in review" }) }); toast("Rejected"); outreach(); }
  catch (e) { toast(e.message, false); }
};

// ------------------------------------------------------------ activity
async function activity() {
  const draw = async () => {
    const [led, logs, runs] = await Promise.all([api("/api/ledger?limit=150"), api("/api/logs?limit=150"), api("/api/runs?limit=10")]);
    const tot = led.summary.reduce((s, x) => s + x.cost_usd, 0);
    $("#view").innerHTML = `
      <h1 class="text-2xl font-bold mb-6">System activity</h1>
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        ${kpi("API & model calls", led.summary.reduce((s, x) => s + x.calls, 0))}
        ${kpi("Retries", led.summary.reduce((s, x) => s + x.retries, 0))}
        ${kpi("Failures", led.summary.reduce((s, x) => s + x.errors, 0), "each logged, fallback source used")}
        ${kpi("Spend", usd(tot), "all runs")}
      </div>
      <div class="card mb-6 overflow-x-auto"><div class="px-5 py-4 border-b font-semibold">Cost ledger by operation</div>
        <table class="w-full text-sm"><thead class="text-left text-xs uppercase text-slate-500"><tr><th>Provider</th><th>Operation</th><th>Calls</th><th>OK</th><th>Retries</th><th>Errors</th><th>Avg ms</th><th class="text-right">Cost</th></tr></thead>
        <tbody>${led.summary.map((x) => `<tr class="border-t"><td>${esc(x.provider)}</td><td>${esc(x.operation)}</td><td>${x.calls}</td><td>${x.ok}</td><td>${x.retries}</td><td>${x.errors}</td><td>${x.avg_ms ?? "-"}</td><td class="text-right">${usd(x.cost_usd)}</td></tr>`).join("")}</tbody></table></div>
      <div class="card mb-6"><div class="px-5 py-4 border-b font-semibold">Workflow recovery</div>
        <table class="w-full text-sm"><tbody>${runs.map((r) => `<tr class="border-t"><td class="mono text-xs">${esc(r.id)}</td><td>${pill(r.status)}</td><td>attempts ${r.attempts}</td><td>${r.progress.recovered ? `recovered ×${r.progress.recovered}, ${r.progress.cache_hits} steps from cache` : "-"}</td><td>${usd(r.progress.cost_usd)}</td></tr>`).join("")}</tbody></table></div>
      <div class="grid lg:grid-cols-2 gap-6">
        <div class="card"><div class="px-5 py-4 border-b font-semibold">Agent log</div><div class="max-h-[32rem] overflow-auto text-xs mono divide-y">${logs.map((l) => `<div class="px-4 py-2 ${l.level === "ERROR" ? "text-red-700" : l.level === "WARN" ? "text-amber-700" : ""}">${new Date(l.ts * 1000).toLocaleTimeString()} [${esc(l.level)}] ${esc(l.step || "")}: ${esc(l.message)}</div>`).join("")}</div></div>
        <div class="card"><div class="px-5 py-4 border-b font-semibold">Recent calls</div><div class="max-h-[32rem] overflow-auto text-xs mono divide-y">${led.entries.map((e) => `<div class="px-4 py-2 ${e.status === "ok" ? "" : "text-amber-700"}">${new Date(e.ts * 1000).toLocaleTimeString()} ${esc(e.provider)}.${esc(e.operation)} ${esc(e.status)} ${e.latency_ms ?? ""}ms ${e.cost_usd ? usd(e.cost_usd) : ""}<div class="text-slate-400 truncate">${esc(e.detail)}</div></div>`).join("")}</div></div>
      </div>`;
  };
  await draw(); poll(draw, 5000);
}

sidebar();
router();
