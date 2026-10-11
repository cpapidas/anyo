"use strict";
const $ = (id) => document.getElementById(id);
const node = (tag, text, cls) => { const el = document.createElement(tag); if (text != null) el.textContent = text; if (cls) el.className = cls; return el; };
const short = (value) => value ? value.slice(0, 10) : "—";
const date = (value) => value ? new Date(value * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
const login = $("login-form");
if (login) {
  login.addEventListener("submit", async (event) => {
    event.preventDefault(); const button = login.querySelector("button"); button.disabled = true; $("login-error").textContent = "";
    try {
      const response = await fetch("/ui/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: $("username").value, password: $("password").value }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || "Unable to sign in.");
      $("password").value = ""; window.location.replace("/ui/");
    } catch (error) { $("login-error").textContent = error.message; } finally { button.disabled = false; }
  });
} else {
  const state = { csrf: "", runs: [], workers: [], view: "runs", selected: null, visible: 100, busy: false, stale: 60, logCursor: 0, detailCursor: 0, logCount: 0, detailCount: 0, logGeneration: 0, detailGeneration: 0, filters: {}, noticeTimer: null };
  const status = (value) => node("span", value === "running-worker" ? "running" : value, `status ${value}`);
  async function api(path, method = "GET", body = undefined) {
    const response = await fetch(`/ui/${path}`, { method, headers: method === "POST" ? { "X-Anyo-CSRF": state.csrf, "Content-Type": "application/json" } : {}, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store" });
    if (response.status === 401) { window.location.replace("/ui/"); throw new Error("Session expired."); }
    const data = await response.json(); if (!response.ok) throw new Error(data.error || "Request failed."); return data;
  }
  function notice(text) { $("notice").textContent = text; $("notice").hidden = false; clearTimeout(state.noticeTimer); state.noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 10000); }
  function fail(error) { $("error").textContent = error.message; $("error").hidden = false; $("connection-dot").classList.add("off"); $("connection-text").textContent = "Connection unavailable"; }
  function switchView(view) {
    state.view = view;
    for (const name of ["runs", "workers", "logs", "detail"]) $(`${name}-view`).hidden = name !== view;
    document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === (view === "detail" ? "runs" : view)));
    const names = { runs: ["Runs", "Every execution. From event to outcome."], workers: ["Workers", "See where work runs. Control what happens next."], logs: ["Logs", "One place for pipeline, runtime, and agent activity."], detail: ["Run details", "The state and activity of a single execution."] };
    $("page-title").textContent = names[view][0]; $("breadcrumb").textContent = names[view][0]; $("page-subtitle").textContent = names[view][1];
    refresh();
  }
  function workerFor(run) { return state.workers.find((worker) => run.owner && run.owner.startsWith(worker.id + ":")); }
  function renderRuns() {
    $("stat-total").textContent = state.runs.length; $("nav-count").textContent = state.runs.filter((r) => ["running", "queued"].includes(r.status)).length;
    for (const key of ["running", "queued"]) $(`stat-${key}`).textContent = state.runs.filter((r) => r.status === key).length;
    $("stat-attention").textContent = state.runs.filter((r) => ["failed", "interrupted"].includes(r.status)).length;
    const search = $("run-search").value.toLowerCase(); const filter = $("run-status").value;
    const rows = state.runs.filter((run) => (!filter || run.status === filter) && `${run.id} ${run.hook_id}`.toLowerCase().includes(search)).sort((a, b) => (b.created_at || b.available_at) - (a.created_at || a.available_at));
    $("runs-body").replaceChildren(); $("runs-empty").hidden = rows.length > 0; $("load-runs").hidden = rows.length <= state.visible;
    $("run-count").textContent = `${rows.length} ${rows.length === 1 ? "run" : "runs"}`;
    for (const run of rows.slice(0, state.visible)) {
      const row = node("tr"); const first = node("td"); first.append(node("strong", run.hook_id), node("small", short(run.id))); row.append(first);
      const outcome = node("td"); outcome.append(status(run.status)); if (run.control && run.status === "running") outcome.append(node("span", `${run.control.replaceAll("_", " ")} requested`, "request-label")); row.append(outcome, node("td", String(run.attempt)), node("td", date(run.created_at)), node("td", workerFor(run) ? short(workerFor(run).id) : "—"));
      const action = node("td"); const open = node("button", "View →"); open.setAttribute("aria-label", `View run ${run.id}`); open.addEventListener("click", (event) => { event.stopPropagation(); openRun(run.id); }); action.append(open); row.append(action); row.addEventListener("click", () => openRun(run.id)); $("runs-body").append(row);
    }
  }
  function fields(container, values) { container.replaceChildren(); for (const [key, value] of values) container.append(node("dt", key), node("dd", value == null ? "—" : String(value))); }
  function controlButton(label, resource, id, action, message, dangerous = false, expectedToken = undefined) {
    const button = node("button", label, dangerous ? "danger" : "");
    button.addEventListener("click", async () => {
      $("confirm-title").textContent = `${label}?`; $("confirm-message").textContent = message; $("confirm-target").textContent = id;
      if (resource === "workers") { const worker = state.workers.find((w) => w.id === id); if (worker?.active_run_ids?.length) $("confirm-target").textContent += `\n\nActive runs:\n${worker.active_run_ids.join("\n")}`; }
      const dialog = $("confirm-dialog"); dialog.returnValue = ""; dialog.showModal();
      dialog.addEventListener("close", async () => { if (dialog.returnValue !== "confirm") return; button.disabled = true; try { const result = await api(`api/${resource}/${encodeURIComponent(id)}/${action}`, "POST", expectedToken === undefined ? undefined : { expected_token: expectedToken }); notice(result.message); await refresh(); } catch (error) { fail(error); } finally { button.disabled = false; } }, { once: true });
    }); return button;
  }
  function renderWorkers() {
    $("workers-list").replaceChildren(); $("workers-empty").hidden = state.workers.length > 0;
    for (const worker of state.workers) {
      const stale = Date.now() / 1000 - worker.heartbeat_at > state.stale; const card = node("article", null, "worker-card");
      const heading = node("div", null, "worker-heading"); heading.append(node("strong", short(worker.id)), status(stale && worker.state !== "stopped" ? "unreachable" : worker.state === "running" ? "running-worker" : worker.state)); card.append(heading);
      const details = node("dl"); fields(details, [["Host", worker.hostname || "—"], ["Execution", worker.execution_mode], ["Active runs", worker.active_run_ids.length], ["Last heartbeat", date(worker.heartbeat_at)]]); details.lastChild.dataset.heartbeat = worker.id; card.append(details);
      if (worker.command && worker.state !== "stopped") card.append(node("p", `${worker.command.replaceAll("_", " ")} requested`, "request-label"));
      const actions = node("div", null, "worker-actions");
      if (worker.state !== "stopped" && !stale) {
        if (worker.state !== "draining") actions.append(controlButton("Stop worker", "workers", worker.id, "stop", "Stop accepting new runs and let active work finish. Other workers will continue."));
        if (worker.execution_mode === "process") actions.append(controlButton("Force stop", "workers", worker.id, "force_stop", "Terminate this worker's active executions. Completed external actions cannot be undone.", true));
      }
      if (worker.execution_mode !== "process") card.append(node("p", "Force stop requires process mode.", "footnote"));
      card.append(actions); $("workers-list").append(card);
    }
  }
  function openRun(id) { state.selected = id; state.detailCursor = 0; state.detailCount = 0; state.detailGeneration++; $("detail-logs").replaceChildren(); $("detail-gap").hidden = true; renderDetail(); switchView("detail"); }
  function renderDetail() {
    const run = state.runs.find((item) => item.id === state.selected);
    if (!run) { $("detail-hook").textContent = "Run no longer retained"; $("detail-status").replaceChildren(); $("detail-fields").replaceChildren(); $("detail-actions").replaceChildren(); return; }
    $("detail-hook").textContent = run.hook_id; $("detail-status").replaceChildren(status(run.status));
    fields($("detail-fields"), [["Run ID", run.id], ["Attempt", run.attempt], ["Created", date(run.created_at)], ["Started", date(run.started_at)], ["Finished", date(run.finished_at)], ["Available after", date(run.available_at)], ["Worker owner", run.owner], ["Error", run.error], ["Control request", run.control ? `${run.control} · ${date(run.control_requested_at)}` : "—"]]);
    const actions = $("detail-actions"); actions.replaceChildren();
    if (["failed", "interrupted", "cancelled"].includes(run.status)) actions.append(controlButton("Retry run", "runs", run.id, "retry", "Execute this run again. Any previous external effects may be repeated."));
    if (["queued", "running"].includes(run.status)) {
      actions.append(controlButton("Cancel run", "runs", run.id, "cancel", "Request cancellation of this execution. The request stays pending until the worker confirms the outcome.", false, run.attempt_token || ""));
      const worker = workerFor(run); if (run.status === "running" && worker?.execution_mode === "process") actions.append(controlButton("Force stop run", "runs", run.id, "force_stop", "Terminate this execution and its owned processes. Other runs continue. Completed external actions cannot be undone.", true, run.attempt_token || ""));
      if (worker?.execution_mode === "thread") actions.append(node("p", "Synchronous thread work must return before cancellation completes. Force stop requires process mode."));
    }
    if (run.control && run.status === "running") actions.append(node("p", "A request is pending. Execution may still be active."));
    else if (run.error === "anyo.cancel_unconfirmed") actions.append(node("p", "Ownership was lost before cancellation could be confirmed. External work may still be active."));
    else if (run.status === "cancelled") actions.append(node("p", "Cancellation confirmed. Previous external actions have not been undone."));
  }
  function renderLogEntry(entry) {
    const row = node("div", null, "log-row"); const level = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].includes(entry.level) ? entry.level : "INFO";
    row.append(node("span", new Date(entry.timestamp * 1000).toLocaleTimeString(), "log-time"), node("span", entry.level, `log-level ${level}`), node("span", entry.source, "log-source"));
    const message = node("div"); message.append(node("pre", entry.message, "log-message"));
    const meta = []; if (entry.run_id) meta.push(`run ${short(entry.run_id)}`); if (entry.attempt != null) meta.push(`attempt ${entry.attempt}`); if (entry.prompt_id) meta.push(`prompt ${short(entry.prompt_id)}`); if (entry.worker_id) meta.push(`worker ${short(entry.worker_id)}`);
    if (meta.length) message.append(node("small", meta.join(" · "), "log-meta")); row.append(message); return row;
  }
  async function fetchLogs(detail = false) {
    const cursorKey = detail ? "detailCursor" : "logCursor"; const generationKey = detail ? "detailGeneration" : "logGeneration"; const generation = state[generationKey]; const requestedCursor = state[cursorKey];
    const query = new URLSearchParams(detail ? { run_id: state.selected } : state.filters); query.set("after", String(state[cursorKey]));
    const page = await api(`api/logs?${query}`); if (generation !== state[generationKey] || requestedCursor !== state[cursorKey]) return;
    const output = $(detail ? "detail-logs" : "logs-output"); const nearEnd = output.scrollHeight - output.scrollTop - output.clientHeight < 80;
    if (page.gap) $(detail ? "detail-gap" : "log-gap").hidden = false;
    for (const entry of page.entries) output.append(renderLogEntry(entry));
    state[cursorKey] = page.next_cursor; state[detail ? "detailCount" : "logCount"] += page.entries.length;
    while (output.children.length > 1000) { output.firstChild.remove(); $(detail ? "detail-gap" : "log-gap").hidden = false; }
    $(detail ? "detail-empty" : "logs-empty").hidden = output.children.length > 0;
    if (!detail) $("log-count").textContent = `${output.children.length} entries shown`;
    if (nearEnd && page.entries.length) output.scrollTop = output.scrollHeight;
  }
  async function refresh() {
    if (state.busy || !state.csrf) return; state.busy = true;
    try {
      const [runs, workers] = await Promise.all([api("api/runs"), api("api/workers")]); const runsChanged = JSON.stringify(state.runs.map(({lease_until, ...r}) => r)) !== JSON.stringify(runs.runs.map(({lease_until, ...r}) => r)); const workersSignature = JSON.stringify(workers.workers.map(({heartbeat_at, ...w}) => ({ ...w, stale: Date.now() / 1000 - heartbeat_at > state.stale }))); const workersChanged = workersSignature !== state.workerSignature; state.workerSignature = workersSignature; const initial = !state.loaded; state.runs = runs.runs; state.workers = workers.workers; state.loaded = true; if (runsChanged || workersChanged || initial) renderRuns(); if (workersChanged || initial) renderWorkers(); for (const element of document.querySelectorAll("[data-heartbeat]")) { const worker = state.workers.find((w) => w.id === element.dataset.heartbeat); if (worker) element.textContent = date(worker.heartbeat_at); }
      if (state.view === "detail") { if (runsChanged || workersChanged) renderDetail(); await fetchLogs(true); }
      if (state.view === "logs" && $("log-follow").checked) await fetchLogs();
      $("error").hidden = true; $("connection-dot").classList.remove("off"); $("connection-text").textContent = "Connected"; $("updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    } catch (error) { fail(error); } finally { state.busy = false; }
  }
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  $("refresh").addEventListener("click", refresh); $("back-runs").addEventListener("click", () => switchView("runs"));
  $("run-search").addEventListener("input", renderRuns); $("run-status").addEventListener("change", renderRuns); $("load-runs").addEventListener("click", () => { state.visible += 100; renderRuns(); });
  function applyFilters() { state.filters = {}; for (const [id, key] of [["log-run", "run_id"], ["log-worker", "worker_id"], ["log-source", "source"], ["log-level", "level"]]) if ($(id).value.trim()) state.filters[key] = $(id).value.trim(); state.logCursor = 0; state.logCount = 0; state.logGeneration++; $("logs-output").replaceChildren(); $("log-gap").hidden = true; fetchLogs().catch(fail); }
  $("apply-log-filters").addEventListener("click", applyFilters); $("log-follow").addEventListener("change", refresh);
  $("detail-open-logs").addEventListener("click", () => { $("log-run").value = state.selected; $("log-worker").value = ""; $("log-source").value = ""; $("log-level").value = ""; applyFilters(); switchView("logs"); });
  $("logout").addEventListener("click", async () => { try { await api("logout", "POST"); window.location.replace("/ui/"); } catch (error) { fail(error); } });
  api("api/session").then((session) => { state.csrf = session.csrf; state.stale = session.stale_seconds; $("namespace").textContent = session.namespace; refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 2000); }).catch(fail);
}
