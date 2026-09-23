/* Multisport Helper page. Plain JavaScript, no external libraries, so it works offline at venues. */
(() => {
  "use strict";

  // ================================================================ helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, word, many = word + "s") => `${n} ${n === 1 ? word : many}`;
  const fold = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const debounce = (fn, ms) => { let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); }; };
  const clock = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
  function ago(ts, now) {
    const s = Math.max(0, Math.round(now - ts));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return clock(ts);
  }

  class ApiError extends Error {}

  async function api(method, url, body) {
    const options = { method, headers: { "X-Requested-With": "multisport" } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(url, options);
    } catch (err) {
      throw new ApiError("Can't reach the Multisport Helper");
    }
    if (response.status === 401) {
      location.href = "/login?next=/";
      throw new ApiError("Please log in again");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(data.error || `Request failed (${response.status})`);
    return data;
  }

  function toast(message, kind = "ok") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    $("#toasts").append(el);
    setTimeout(() => el.remove(), kind === "error" ? 9000 : 4500);
  }
  const fail = (err) => toast(err.message || String(err), "error");

  function confirmBox({ title, body = [], ok = "OK", danger = false, cancel = "Cancel" }) {
    const dlg = $("#dlg-confirm");
    $("#confirm-title").textContent = title;
    $("#confirm-body").replaceChildren(...[].concat(body).map((line) => {
      const p = document.createElement("p");
      p.textContent = line;
      return p;
    }));
    const okButton = $("#confirm-ok");
    okButton.textContent = ok;
    okButton.className = `btn ${danger ? "danger" : "primary"}`;
    $("#confirm-cancel").textContent = cancel;
    dlg.returnValue = "";
    dlg.showModal();
    (danger ? $("#confirm-cancel") : okButton).focus();
    return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
  }

  // per-browser conveniences only: which tab is open and which problems were ticked off
  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(`ms.${key}`); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(`ms.${key}`, JSON.stringify(value)); } catch { /* storage unavailable */ }
    },
  };

  $$("[data-close]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close("cancel")));

  // ================================================================ state
  const S = {
    status: null,
    version: null,
    analysis: null,
    level: "all",
    done: store.get("done", {}),   // {raceId: {problemId: sig}}
    settings: null,
    settingsDirty: false,
  };
  const raceKey = () => String(S.status?.race_id || "");
  const doneMap = () => (S.done[raceKey()] ||= {});
  const isDone = (p) => doneMap()[p.id] === p.sig;   // a changed result shows up again

  // ================================================================ tabs
  function showTab(name) {
    if (!["problems", "all", "settings"].includes(name)) name = "problems";
    $$(".tab").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.tab === name)));
    $$(".tab-panel").forEach((panel) => { panel.hidden = panel.id !== `tab-${name}`; });
    store.set("tab", name);
    history.replaceState(null, "", `#${name}`);
    if (name === "settings") loadSettings().catch(fail);
  }
  $$(".tab").forEach((tab) => tab.addEventListener("click", async () => {
    if (tab.dataset.tab === "settings" || !S.settingsDirty
      || await confirmBox({ title: "Discard settings changes?", body: ["Your changes on the Settings tab haven't been saved."], ok: "Discard", danger: true })) {
      if (S.settingsDirty && tab.dataset.tab !== "settings") { S.settingsDirty = false; $("#settings-dirty").hidden = true; }
      showTab(tab.dataset.tab);
    }
  }));
  $$("[data-goto]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showTab(a.dataset.goto); }));

  const topbar = $("#topbar");
  new ResizeObserver(() => document.documentElement.style.setProperty("--topbar-h", `${topbar.offsetHeight}px`)).observe(topbar);

  // ================================================================ header & status
  function setStatus(cls, text, title) {
    const el = $("#st-webscorer");
    el.querySelector(".dot").className = `dot ${cls}`;
    el.querySelector("b").textContent = text;
    el.title = title || text;
  }

  function renderHeader(st) {
    const race = st.race;
    $("#hdr-race").textContent = race ? `${race.name}${race.date ? " · " + race.date : ""}` : (st.race_id ? `Race ${st.race_id}` : "No race loaded");
    document.title = race ? `${race.name} · Multisport Helper` : "Multisport Helper";
    const input = $("#race-id");
    if (document.activeElement !== input && !input.dataset.touched) input.value = st.race_id || "";

    $("#live-switch").checked = st.live;
    $("#live-toggle").classList.toggle("on", st.live);
    $("#live-label").textContent = st.live ? "LIVE UPDATE ON" : "LIVE UPDATE OFF";
    const f = st.fetch || {};
    $("#live-counts").textContent = f.ok_at ? `updated ${ago(f.ok_at, st.now)}` : "";

    if (!st.race_id) setStatus("", "no race");
    else if (f.error) setStatus("err", "error", f.error);
    else if (f.taps_error) setStatus("warn", "no taps", `Results OK, but taps couldn't be fetched: ${f.taps_error}`);
    else if (f.ok_at) setStatus(st.live ? "ok" : "warn", st.live ? ago(f.ok_at, st.now) : "paused", `Last fetched at ${clock(f.ok_at)}`);
    else setStatus("", "waiting");

    $("#setup-notice").hidden = st.configured;
  }

  let statusTimer = null;
  let statusBusy = false;
  async function pollStatus() {
    clearTimeout(statusTimer);
    if (statusBusy) return;
    statusBusy = true;
    try {
      const st = await api("GET", "/api/status");
      $("#offline").hidden = true;
      S.status = st;
      renderHeader(st);
      if (st.version !== S.version) await loadAnalysis();
    } catch (err) {
      $("#offline").hidden = false;
    } finally {
      statusBusy = false;
      statusTimer = setTimeout(pollStatus, document.hidden ? 8000 : 2000);
    }
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollStatus(); });

  async function loadAnalysis() {
    const data = await api("GET", "/api/analysis");
    S.version = data.version;
    S.analysis = data.analysis;
    fillDistanceFilters();
    renderProblems();
    renderAll();
  }

  // ================================================================ race, live, refresh
  $("#race-id").addEventListener("input", (e) => { e.target.dataset.touched = "1"; });
  $("#race-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#race-id");
    const raceId = input.value.trim().replace(/.*raceid=(\d+).*/i, "$1");  // a pasted results link works too
    input.value = raceId;
    const button = $("#btn-load");
    button.disabled = true;
    button.textContent = "Loading…";
    try {
      const st = await api("POST", "/api/race", { race_id: raceId });
      delete input.dataset.touched;
      toast(st.race ? `Loaded ${st.race.name}: ${plural(st.problems, "problem")}` : "Loaded");
      await pollStatus();
    } catch (err) {
      fail(err);
    } finally {
      button.disabled = false;
      button.textContent = "Load";
    }
  });

  $("#live-switch").addEventListener("change", async (e) => {
    const want = e.target.checked;
    e.target.checked = !want; // unchanged until the app confirms
    try {
      await api("POST", "/api/live", { on: want });
      toast(want ? "Live update ON" : "Live update OFF", want ? "ok" : "warn");
      await pollStatus();
    } catch (err) { fail(err); }
  });

  $("#btn-refresh").addEventListener("click", async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      await api("POST", "/api/refresh");
      await pollStatus();
      toast("Refreshed");
    } catch (err) { fail(err); } finally { button.disabled = false; }
  });

  // ================================================================ problems tab
  function fillDistanceFilters() {
    const names = (S.analysis?.distances || []).map((d) => d.name);
    for (const id of ["#p-distance", "#a-distance"]) {
      const sel = $(id);
      const keep = sel.value;
      sel.innerHTML = `<option value="">All distances</option>` + names.map((n) => `<option>${esc(n)}</option>`).join("");
      sel.value = names.includes(keep) ? keep : "";
    }
  }

  $$(".seg button").forEach((b) => b.addEventListener("click", () => {
    S.level = b.dataset.level;
    $$(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderProblems();
  }));
  $("#p-distance").addEventListener("change", () => renderProblems());
  $("#p-search").addEventListener("input", debounce(() => renderProblems(), 150));
  $("#show-done").addEventListener("change", () => renderProblems());

  function matches(p, q) {
    if (!q) return true;
    return fold(p.bib) === q || fold(p.name).includes(q) || fold(p.bib).startsWith(q);
  }

  function renderSummary() {
    const a = S.analysis;
    const box = $("#summary");
    if (!a) { box.hidden = true; return; }
    const errors = a.problems.filter((p) => p.level === "error").length;
    const checks = a.problems.length - errors;
    const c = a.counts || {};
    box.hidden = false;
    box.innerHTML = `
      <div class="stat err"><span class="big">${errors}</span><span class="label">can't be right</span></div>
      <div class="stat warn"><span class="big">${checks}</span><span class="label">worth a look</span></div>
      <div class="stat"><span class="big">${c.finished || 0}</span><span class="label">finished</span></div>
      <div class="stat"><span class="big">${c["on course"] || 0}</span><span class="label">on course</span></div>
      <span class="spacer"></span>
      ${a.taps_available ? "" : `<span class="notice warn small">Raw taps aren't available, so suggestions use the splits only.</span>`}
      ${a.race.state ? `<span class="muted small">${esc(a.race.state)}</span>` : ""}`;
    $("#tab-count").textContent = a.problems.length ? `(${a.problems.filter((p) => !isDone(p)).length})` : "";
  }

  function renderProblems() {
    renderSummary();
    const a = S.analysis;
    const cards = $("#cards");
    const empty = $("#p-empty");
    $("#dist-notes").innerHTML = (a?.distances || []).flatMap((d) => (d.notes || []).map((n) =>
      `<div class="notice info"><b>${esc(d.name)}:</b> ${esc(n)}</div>`)).join("");
    if (!a) {
      cards.innerHTML = "";
      empty.hidden = false;
      empty.innerHTML = S.status?.race_id
        ? `<h2>Nothing loaded yet</h2><p>Choose <b>Load</b> or <b>Refresh now</b> to fetch race ${esc(S.status.race_id)}.</p>`
        : `<h2>No race loaded</h2><p>Enter the Webscorer race ID at the top and choose <b>Load</b>. Then switch <b>Live Update</b> on.</p>`;
      return;
    }
    const q = fold($("#p-search").value.trim());
    const dist = $("#p-distance").value;
    const showDone = $("#show-done").checked;
    const all = a.problems.filter((p) => (!dist || p.distance === dist) && matches(p, q)
      && (S.level === "all" || p.level === S.level));
    const doneCount = a.problems.filter(isDone).length;
    $("#done-count").textContent = doneCount ? `(${doneCount})` : "";
    const visible = all.filter((p) => showDone || !isDone(p));

    // missing splits that affect lots of finishers are shown once per distance, not one card each
    const groups = new Map();
    const singles = [];
    for (const p of visible) {
      if (p.grouped) {
        const key = `${p.distance}|${p.grouped}`;
        if (!groups.has(key)) groups.set(key, { distance: p.distance, end: p.grouped, items: [] });
        groups.get(key).items.push(p);
      } else singles.push(p);
    }
    cards.innerHTML = singles.map(cardHtml).join("") + [...groups.values()].map(groupHtml).join("");
    empty.hidden = visible.length > 0;
    if (!visible.length) {
      empty.innerHTML = a.problems.length
        ? `<h2>Nothing to show</h2><p>${doneCount ? "Everything left is marked done. " : ""}Try another filter.</p>`
        : `<h2>No problems found</h2><p>Every result in ${esc(a.race.name)} looks right.</p>`;
    }
  }

  function legsTable(p) {
    const s = p.suggestion;
    const head = p.legs.map((l) => `<th title="${esc(l.full)}">${esc(l.name)}</th>`).join("");
    const now = p.legs.map((l) => `<td class="${l.bad ? "bad" : ""}">${esc(l.time || "–")}</td>`).join("");
    let fixed = "";
    if (s) {
      fixed = `<tr><th class="rowhead">Suggested</th>${s.legs.map((l) =>
        `<td class="${l.changed ? "fix" : ""} ${l.changed && l.estimate ? "est" : ""}">${esc(l.new || "–")}</td>`).join("")}
        <td class="${s.finish_changed ? "fix" : ""}">${esc(s.new_total || "")}</td></tr>`;
    }
    const usual = p.legs.map((l) => `<td>${esc(l.usual)}</td>`).join("");
    return `<table class="legs">
      <tr><th class="rowhead"></th>${head}<th>Total</th></tr>
      <tr><th class="rowhead">Now</th>${now}<td>${esc(p.time || "")}</td></tr>
      ${fixed}
      <tr class="usual"><th class="rowhead">Usual</th>${usual}<td></td></tr>
    </table>`;
  }

  function fixesHtml(s) {
    if (!s) return `<p class="hint" style="margin:0">No better reading of the taps was found. Check this racer by hand (photos, marshal notes, lap counter).</p>`;
    const rows = s.ends.filter((e) => e.kind !== "same" && e.kind !== "later");
    const table = `<table class="fixes">
      <tr><th>Tap</th><th>Now</th><th>Set it to</th><th></th></tr>
      ${rows.map((e) => `<tr class="${e.kind}">
        <td>${esc(e.end)}</td>
        <td>${esc(e.now || "none")}</td>
        <td class="new">${esc(e.new)}</td>
        <td class="note">${esc(e.note)}${e.chips?.length ? `<div class="chips">Unassigned chip reads nearby: ${e.chips.map((c) =>
          `${esc(c.tod)} (chip ${esc(c.chip)}, #${esc(c.seq)})`).join(", ")}</div>` : ""}</td>
      </tr>`).join("")}
    </table>`;
    const removes = s.remove.length ? `<ul class="removes">${s.remove.map((r) =>
      `<li>Delete the tap at <b>${esc(r.tod || r.elapsed)}</b>${r.source ? ` (${esc(r.source)})` : ""}, now used as the ${esc(r.was)}: ${esc(r.why)}.</li>`).join("")}</ul>` : "";
    const alt = s.alternative ? `<div class="alt"><b>Also possible:</b> ${s.alternative.ends.map((e) =>
      `${esc(e.end)} ${esc(e.new)}`).join(", ")}. Legs would be ${s.alternative.legs.map((l) => `${esc(l.name)} ${esc(l.new || "–")}`).join(", ")}.</div>` : "";
    return table + removes + alt;
  }

  function tapsHtml(p) {
    if (!p.timeline.length) return "";
    return `<details class="taps"><summary>Raw taps (${p.timeline.length})</summary>
      <table class="taps"><tr><th>#</th><th>Webscorer</th><th>Time of day</th><th>Race time</th><th>Reader</th><th>Used as</th></tr>
      ${p.timeline.map((t) => `<tr><td>${esc(t.seq)}</td><td>${esc(t.label)}</td><td>${esc(t.tod)}</td><td>${esc(t.elapsed)}</td>
        <td class="${t.odd_reader ? "odd" : ""}" title="${t.odd_reader ? "Not the reader that usually records this" : ""}">${esc(t.reader)}</td><td>${esc(t.used)}</td></tr>`).join("")}
      </table></details>`;
  }

  function cardHtml(p) {
    const done = isDone(p);
    return `<article class="card pcard ${p.level}" data-id="${esc(p.id)}">
      <div class="pcard-head">
        <span class="bib">${esc(p.bib)}</span><span class="who">${esc(p.name)}</span>
        <span class="dist">${esc(p.distance)}${p.status !== "finished" ? ` · ${esc(p.status)}` : ""}</span>
        <span class="spacer"></span>
        <span class="level ${p.level}">${p.level === "error" ? "Can't be right" : "Worth a look"}</span>
      </div>
      <ul class="reasons">${p.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      <div class="pcard-body">
        <div style="overflow-x:auto">${legsTable(p)}</div>
        <div><h3>Suggested fix in Webscorer</h3>${fixesHtml(p.suggestion)}</div>
        ${tapsHtml(p)}
      </div>
      <div class="pcard-foot">
        <button class="btn small" type="button" data-done="${esc(p.id)}">${done ? "Not done" : "Done"}</button>
        <span class="hint">${done ? "Marked done. It comes back if these times change." : "Mark done once it's fixed or checked. It comes back if the times change."}</span>
      </div>
    </article>`;
  }

  function groupHtml(g) {
    return `<article class="card group-card">
      <b>${esc(g.distance)}: ${plural(g.items.length, "finisher")} with no ${esc(g.end)} time</b>
      <p class="hint" style="margin:4px 0 0">Nothing else looks wrong for these, so the other splits are fine. Add the missing taps if you have them (hand timing, photos).</p>
      <div class="bibs">${g.items.map((p) => `<span title="${esc(p.name)}">${esc(p.bib)}</span>`).join("")}</div>
    </article>`;
  }

  $("#cards").addEventListener("click", (e) => {
    const button = e.target.closest("[data-done]");
    if (!button) return;
    const p = S.analysis.problems.find((x) => x.id === button.dataset.done);
    if (!p) return;
    const map = doneMap();
    if (isDone(p)) delete map[p.id]; else map[p.id] = p.sig;
    store.set("done", S.done);
    renderProblems();
  });

  // ================================================================ all finishers tab
  $("#a-distance").addEventListener("change", () => renderAll());
  $("#a-search").addEventListener("input", debounce(() => renderAll(), 150));
  $("#a-problems").addEventListener("change", () => renderAll());
  $("#a-dns").addEventListener("change", () => renderAll());

  function renderAll() {
    const a = S.analysis;
    const wrap = $("#all-wrap");
    const empty = $("#a-empty");
    if (!a) { wrap.innerHTML = ""; empty.hidden = false; empty.innerHTML = "<h2>No race loaded</h2>"; return; }
    const q = fold($("#a-search").value.trim());
    const dist = $("#a-distance").value;
    const onlyProblems = $("#a-problems").checked;
    const withDns = $("#a-dns").checked;
    let shown = 0;
    wrap.innerHTML = a.distances.filter((d) => !dist || d.name === dist).map((d) => {
      const rows = d.rows.filter((r) => (withDns || !["DNS", "not started"].includes(r.status))
        && (!onlyProblems || r.problem) && matches(r, q));
      shown += rows.length;
      if (!rows.length) return "";
      return `<div class="dist-head"><h2>${esc(d.name)}</h2><span class="muted">${plural(rows.length, "racer")}</span></div>
        <div class="table-wrap"><table class="data finishers">
        <thead><tr><th>Bib</th><th>Name</th>${d.legs.map((l) => `<th class="leg" title="${esc(l.name)}">${esc(l.short)}</th>`).join("")}<th class="leg">Time</th><th>Status</th></tr></thead>
        <tbody>
        <tr class="usual"><td></td><td>Usual</td>${d.legs.map((l) => `<td class="leg">${esc(l.usual)}</td>`).join("")}<td></td><td></td></tr>
        ${rows.map((r) => `<tr class="${r.problem ? "problem" : ""} ${r.status === "finished" || r.status === "on course" ? "" : "muted"}">
          <td class="bib">${esc(r.bib)}</td><td>${esc(r.name)}</td>
          ${r.legs.map((t, i) => `<td class="leg ${r.bad.includes(i) ? "bad" : ""}">${esc(t)}</td>`).join("")}
          <td class="leg">${esc(r.time)}</td><td>${esc(r.status)}</td></tr>`).join("")}
        </tbody></table></div>`;
    }).join("");
    empty.hidden = shown > 0;
    if (!shown) empty.innerHTML = "<h2>Nobody matches</h2>";
  }

  // ================================================================ settings tab
  const form = $("#settings-form");
  const FIELDS = ["webscorer_api_id", "refresh_seconds", "transition_multiple", "transition_extra_min", "discipline_min_pct", "transition_min_pct"];

  async function loadSettings() {
    if (S.settingsDirty) return;
    S.settings = await api("GET", "/api/settings");
    fillSettings(S.settings);
  }
  function fillSettings(s) {
    for (const f of FIELDS) form[f].value = s[f] ?? "";
    form.webscorer_token.value = "";
    $("#token-state").innerHTML = s.webscorer_token_set
      ? `<span class="set">✓ Saved</span><button class="btn link" type="button" id="btn-clear-token">Remove</button>`
      : `<span class="muted">Not set. Some races need it.</span>`;
    $("#pw-state").textContent = s.password_set ? "A password is set. Anyone opening this page must enter it."
      : "No password: anyone on this network can open this page.";
    S.settingsDirty = false;
    $("#settings-dirty").hidden = true;
  }
  form.addEventListener("input", (e) => {
    if (e.target.id === "pw-new") return;
    S.settingsDirty = true;
    $("#settings-dirty").hidden = false;
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const values = {};
    for (const f of FIELDS) values[f] = form[f].value;
    if (form.webscorer_token.value.trim()) values.webscorer_token = form.webscorer_token.value.trim();
    try {
      S.settings = await api("PUT", "/api/settings", { values });
      fillSettings(S.settings);
      toast("Settings saved");
    } catch (err) { fail(err); }
  });
  $("#token-state").addEventListener("click", async (e) => {
    if (e.target.id !== "btn-clear-token") return;
    if (!(await confirmBox({ title: "Remove the API private key?", ok: "Remove", danger: true }))) return;
    try { S.settings = await api("POST", "/api/settings/clear-token"); fillSettings(S.settings); toast("Key removed"); } catch (err) { fail(err); }
  });
  $("#btn-test").addEventListener("click", async (e) => {
    if (S.settingsDirty) { toast("Save the settings first", "warn"); return; }
    const button = e.currentTarget;
    button.disabled = true;
    try {
      const r = await api("POST", "/api/settings/test", { race_id: $("#race-id").value.trim() });
      toast(`Connected: ${r.name}${r.date ? ` (${r.date})` : ""}${r.taps ? "" : ". Raw taps aren't available with these details."}`, r.taps ? "ok" : "warn");
    } catch (err) { fail(err); } finally { button.disabled = false; }
  });
  $("#btn-pw").addEventListener("click", async () => {
    const pw = $("#pw-new").value;
    if (!pw && !(await confirmBox({ title: "Remove the page password?", ok: "Remove", danger: true }))) return;
    try {
      const r = await api("POST", "/api/settings/password", { password: pw });
      $("#pw-new").value = "";
      toast(r.password_set ? "Password saved" : "Password removed");
      S.settingsDirty = false;
      await loadSettings();
    } catch (err) { fail(err); }
  });

  // ================================================================ start
  showTab((location.hash || "").slice(1) || store.get("tab", "problems"));
  pollStatus();
})();
