const $ = (selector) => document.querySelector(selector);

function appendText(parent, tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text ?? "";
  if (className) node.className = className;
  parent.appendChild(node);
  return node;
}

function formatNumber(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 5 }).format(value)
    : String(value ?? "—");
}

function drawEvents(events) {
  const stream = $("#boundary-stream");
  stream.replaceChildren();
  const recent = [...events].slice(-16).reverse();
  if (!recent.length) {
    stream.className = "stream empty";
    stream.textContent = "Waiting for bank events.";
    return;
  }
  stream.className = "stream";
  for (const row of recent) {
    const card = document.createElement("article");
    card.className = "event-card";
    const message = row.message || {};
    const top = document.createElement("div");
    top.className = "event-top";
    appendText(top, "span", `${message.bank ?? "bank"} · ${row.chain_id ?? "chain"}`);
    appendText(top, "span", `hop ${formatNumber(row.hop_depth)}`);
    card.appendChild(top);
    const payload = {
      src_bid: message.src_bid,
      dst_bid: message.dst_bid,
      amt_bin: message.amt_bin,
      f: message.f,
    };
    appendText(card, "pre", JSON.stringify(payload, null, 2));
    stream.appendChild(card);
  }
}

function drawDecision(state) {
  const decision = $("#decision");
  decision.replaceChildren();
  const alerts = state.alerts || [];
  const alert = alerts.length ? alerts[alerts.length - 1] : null;
  const status = $("#alert-status");
  if (!alert) {
    status.textContent = "No alert";
    status.className = "pill neutral";
    decision.className = "empty";
    decision.textContent = "No alert has fired in the current run.";
    $("#brief").textContent = "No brief available.";
    $("#llm-mode").textContent = "";
    $("#prompt").textContent = "No prompt sent.";
    return;
  }

  status.textContent = alert.type || "Alert";
  status.className = "pill alert";
  decision.className = "";
  const summary = document.createElement("div");
  summary.className = "decision-summary";
  for (const [label, value] of [
    ["Chain", alert.chain_id],
    ["Hop depth", alert.hop_depth],
    ["p_cascade", alert.p_cascade],
    ["anom", alert.anom],
    ["Hold recommendation", alert.target_bid],
  ]) {
    const chip = document.createElement("span");
    chip.className = "pill";
    chip.textContent = `${label}: ${formatNumber(value)}`;
    summary.appendChild(chip);
  }
  decision.appendChild(summary);

  const chain = alert.llm_payload?.chain || [];
  const table = document.createElement("table");
  table.className = "chain-table";
  const head = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const label of ["Bank", "BID", "Δt ms", "Amount bin"]) appendText(headerRow, "th", label);
  head.appendChild(headerRow);
  table.appendChild(head);
  const body = document.createElement("tbody");
  for (const hop of chain) {
    const row = document.createElement("tr");
    for (const value of hop) appendText(row, "td", formatNumber(value));
    body.appendChild(row);
  }
  table.appendChild(body);
  decision.appendChild(table);

  const evidence = document.createElement("div");
  evidence.className = "evidence";
  for (const item of alert.evidence || []) {
    const [feature, percentile] = item;
    appendText(evidence, "span", `${feature} · ${formatNumber(percentile)} percentile`);
  }
  decision.appendChild(evidence);

  const brief = alert.llm_brief || {};
  $("#llm-mode").textContent = brief.llm_mode ? `· ${brief.llm_mode}` : "";
  const briefView = $("#brief");
  briefView.replaceChildren();
  briefView.className = "brief";
  const fields = [
    ["Summary", "summary"],
    ["Pattern", "suspected_pattern"],
    ["Risk", "risk_level"],
    ["Recommended action", "recommended_action"],
    ["Limits", "limits"],
  ];
  const briefGrid = document.createElement("div");
  briefGrid.className = "brief-grid";
  for (const [label, key] of fields) {
    if (!brief[key]) continue;
    const row = document.createElement("div");
    appendText(row, "strong", label);
    appendText(row, "span", brief[key]);
    briefGrid.appendChild(row);
  }
  briefView.appendChild(briefGrid);
  $("#prompt").textContent = alert.llm_prompt || "No prompt sent.";
}

function drawAudit(audit = {}) {
  const counters = $("#audit-counters");
  counters.replaceChildren();
  for (const [label, key] of [
    ["Messages scanned", "messages_scanned"],
    ["Bytes scanned", "bytes_scanned"],
    ["Canary hits", "canary_hits"],
    ["Self-test scans", "self_test_messages"],
    ["Self-test hits", "self_test_hits"],
    ["Validator blocks", "validator_blocks"],
  ]) {
    const box = document.createElement("div");
    box.className = "metric";
    appendText(box, "dt", label);
    appendText(box, "dd", formatNumber(audit[key] ?? 0));
    counters.appendChild(box);
  }
  const hitCount = Number(audit.canary_hits || 0);
  const canary = $("#canary-status");
  canary.textContent = hitCount ? "Canary hit in production traffic" : "No production canary hit";
  canary.className = hitCount ? "pill alert" : "pill good";
}

async function refreshState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error("mesh unavailable");
    const state = await response.json();
    $("#connection").textContent = "Mesh connected";
    $(".live-dot").classList.add("connected");
    drawEvents(state.events || []);
    drawDecision(state);
    try {
      const auditResponse = await fetch("/api/audit", { cache: "no-store" });
      drawAudit(auditResponse.ok ? await auditResponse.json() : state.audit || {});
    } catch (_) {
      drawAudit(state.audit || {});
    }
    if (state.negative_control) {
      $("#negative-result").textContent = `Negative control: ${state.negative_control}`;
    }
  } catch (_) {
    $("#connection").textContent = "Mesh unavailable";
    $(".live-dot").classList.remove("connected");
  }
}

async function refreshPlatform() {
  const output = $("#platform-evidence");
  output.textContent = "Collecting host evidence…";
  try {
    const response = await fetch("/api/platform", { cache: "no-store" });
    output.textContent = JSON.stringify(await response.json(), null, 2);
  } catch (_) {
    output.textContent = "Platform evidence unavailable.";
  }
}

async function refreshResults() {
  const output = $("#results");
  try {
    const response = await fetch("/api/results", { cache: "no-store" });
    const payload = await response.json();
    output.textContent = payload.available
      ? JSON.stringify(payload.results, null, 2)
      : "No measured results file is available.";
  } catch (_) {
    output.textContent = "Measured results unavailable.";
  }
}

async function postControl(path, statusNode, pending, done) {
  statusNode.textContent = pending;
  try {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Request failed");
    statusNode.textContent = done(payload);
    await refreshState();
  } catch (error) {
    statusNode.textContent = error.message;
  }
}

$("#launch").addEventListener("click", (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  postControl("/api/heist", $("#action-status"), "Sending launch request…", () => "Heist sent to the demo runner.")
    .finally(() => { button.disabled = false; });
});

$("#reset").addEventListener("click", (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  postControl("/api/reset", $("#action-status"), "Resetting services…", () => "Demo state reset.")
    .finally(() => { button.disabled = false; });
});

$("#negative-control").addEventListener("click", () => {
  postControl("/api/negative-control", $("#negative-result"), "Running scan…", (payload) => {
    const result = payload.negative_control || "missed";
    return `Negative control: ${result}`;
  });
});

$("#refresh-platform").addEventListener("click", refreshPlatform);

refreshState();
refreshPlatform();
refreshResults();
window.setInterval(refreshState, 1500);
