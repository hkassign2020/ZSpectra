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
  $("#llm-mode").textContent = brief.llm_mode
    ? `· ${brief.llm_mode}`
    : alert.llm_status === "pending" ? "· generating" : "";
  const briefView = $("#brief");
  briefView.replaceChildren();
  briefView.className = "brief";
  if (!alert.llm_brief && alert.llm_status === "pending") {
    briefView.textContent = "Alert is live. Generating the blind-data brief…";
    $("#prompt").textContent = alert.llm_prompt || "No prompt sent.";
    return;
  }
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
  const summary = $("#results-summary");
  try {
    const response = await fetch("/api/results", { cache: "no-store" });
    const payload = await response.json();
    if (!payload.available) {
      output.textContent = "No measured results file is available.";
      summary.className = "results-grid empty";
      summary.textContent = "No measured results file is available.";
      return;
    }
    output.textContent = JSON.stringify(payload.results, null, 2);
    drawResultSummary(payload.results || {});
  } catch (_) {
    output.textContent = "Measured results unavailable.";
    summary.className = "results-grid empty";
    summary.textContent = "Measured results unavailable.";
  }
}

function drawResultSummary(results) {
  const target = $("#results-summary");
  target.replaceChildren();
  target.className = "results-grid";

  const card = (title, lines) => {
    const item = document.createElement("article");
    item.className = "result-card";
    const heading = document.createElement("div");
    heading.className = "result-heading";
    appendText(heading, "h3", title);
    appendText(heading, "span", "synthetic data", "pill synthetic-label");
    item.appendChild(heading);
    for (const line of lines) appendText(item, "p", line, "result-value");
    target.appendChild(item);
  };
  const summaryLine = (value, scale = 1, unit = "") => {
    if (!value || typeof value.mean !== "number" || typeof value.std !== "number") return "No measured value.";
    return `${formatNumber(value.mean * scale)} ± ${formatNumber(value.std * scale)}${unit}`;
  };
  const detection = results.detection || {};
  card("Value still stoppable", [summaryLine(detection.value_still_stoppable_pct, 1, "% mean ± std over seeds")]);
  card("Time to flag from hop 1", [summaryLine(detection.time_to_flag_ms, 1, " ms mean ± std over seeds")]);
  card("Alert to next hop", [summaryLine(detection.alert_to_next_hop_gap_ms, 1, " ms mean ± std over seeds")]);

  const loadTest = results.load_test || {};
  const decisionBySpeed = loadTest.decision_latency_ms || {};
  for (const speed of ["1x", "5x", "10x"]) {
    const metric = decisionBySpeed[speed];
    card(`Decision latency at ${speed} replay`, metric
      ? [
          `p50 ${formatNumber(metric.p50)} ms · p95 ${formatNumber(metric.p95)} ms · p99 ${formatNumber(metric.p99)} ms`,
          `${formatNumber(metric.n)} alert decisions measured`,
        ]
      : ["No measured value."]);
  }

  const zeroDay = detection.zero_day_slow_drip_recall_at_1pct_fpr;
  card("Slow-drip zero-day recall · Model 1 alone", [summaryLine(zeroDay, 100, "% mean ± std over seeds")]);

  const ablations = results.ablation || {};
  for (const [name, label] of [
    ["raw_only", "Ablation · raw features"],
    ["raw_plus_compression", "Ablation · raw + compression"],
    ["compression_only", "Ablation · compression features"],
  ]) {
    card(label, [summaryLine(ablations[name]?.recall_at_1pct_fpr, 100, "% recall mean ± std over seeds")]);
  }

  const zlib = results.zlib_bench || {};
  if (typeof zlib.runtime_version === "string") {
    const support = zlib.zlib_dfltcc_support === true
      ? "supported"
      : zlib.zlib_dfltcc_support === false ? "not found in loaded zlib" : "unknown";
    const lines = [
      `Architecture ${zlib.platform_architecture || "unknown"} · zlib ${zlib.runtime_version}`,
      `DFLTCC acceleration ${zlib.hardware_acceleration_claimable === true ? "verified by the configured comparison" : "not verified"} · CPU flag ${zlib.dflt_flag === true ? "present" : "not observed"} · zlib ${support}`,
      `Application runtime DFLTCC ${zlib.application_runtime_enabled === true ? "enabled" : "disabled"} · level mask ${zlib.application_runtime_level_mask || "not set"}`,
    ];
    for (const row of zlib.rows || []) {
      if (typeof row.hw_on_us === "number" && typeof row.hw_off_us === "number") {
        lines.push(`${formatNumber(row.buffer_bytes)} byte level-six compression · on ${formatNumber(row.hw_on_us)} µs · off ${formatNumber(row.hw_off_us)} µs`);
      } else if (typeof row.baseline_us === "number") {
        lines.push(`${formatNumber(row.buffer_bytes)} byte level-six baseline · ${formatNumber(row.baseline_us)} µs`);
      }
    }
    card("LinuxONE zlib / DFLTCC", lines);
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
