let chart;

/* -----------------------------
   Global app state (persisted)
----------------------------- */
const STORE = {
  convKey: "aiqa_conversation_id",
  specKey: "aiqa_current_spec_id",
};

let activeConversationId = localStorage.getItem(STORE.convKey) || null;
let currentSpecId = localStorage.getItem(STORE.specKey) || null;

/* -----------------------------
   Utilities
----------------------------- */
function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const BRAND = {
  HERO: "/static/branding/aiqa_hero.png",
  SIDEBAR_LOGO: "/static/branding/aiqa_sidebar_logo.png",
  ASK_LOGO: "/static/branding/aiqa_ask_logo.png",
};

function fmtTs(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function relTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  const t = d.getTime();
  if (isNaN(t)) return "";
  const diff = Date.now() - t;
  const sec = Math.floor(diff / 1000);
  const min = Math.floor(sec / 60);
  const hr = Math.floor(min / 60);
  const day = Math.floor(hr / 24);
  if (day > 0) return `${day}d ago`;
  if (hr > 0) return `${hr}h ago`;
  if (min > 0) return `${min}m ago`;
  return `${Math.max(0, sec)}s ago`;
}

function pickArtifacts(obj) {
  const a = obj?.artifacts ?? obj ?? {};
  return {
    pdf: a.pdf || obj?.pdf || null,
    xlsx: a.xlsx || obj?.xlsx || null,
    run_json: a.run_json || obj?.run_json || null,
    report_json: a.report_json || obj?.report_json || null,
  };
}

function badgeClass(failed) {
  return (failed ?? 0) > 0 ? "pill pill-fail" : "pill pill-pass";
}

function statusText(failed) {
  return (failed ?? 0) > 0 ? "FAILED" : "PASSED";
}

function artifactButtons(artifacts) {
  if (!artifacts) return "";
  const { pdf, xlsx, run_json, report_json } = artifacts;

  const btn = (label, file, icon) =>
    file
      ? `<a class="btn btn-secondary btn-sm" href="/api/artifacts/${encodeURIComponent(
          file
        )}" target="_blank" rel="noopener">
           <span class="btn-ico">${icon}</span><span>${label}</span>
         </a>`
      : "";

  const items = [
    btn("PDF", pdf, "📄"),
    btn("Excel", xlsx, "📊"),
    btn("Run JSON", run_json, "🧾"),
    btn("Report JSON", report_json, "🧩"),
  ].filter(Boolean);

  if (!items.length) return "";
  return `<div class="btn-row">${items.join("")}</div>`;
}

/* -----------------------------
   Fetch helpers (auth-safe)
   IMPORTANT: 401 => redirect login
              403 => show error (role denied), do NOT redirect
----------------------------- */
async function fetchJson(url, options = {}) {
  const res = await fetch(url, {
    credentials: "include",
    ...options,
  });

  const ct = (res.headers.get("content-type") || "").toLowerCase();

  // redirect only when truly unauthenticated
  if (res.status === 401) {
    window.location.replace("/login");
    throw new Error(`auth required: ${res.status}`);
  }

  // For HTML responses (login page), redirect
  if (!ct.includes("application/json")) {
    const txt = await res.text().catch(() => "");
    if (txt.includes("<html") || txt.includes("<!doctype")) {
      window.location.replace("/login");
      throw new Error("redirected to login");
    }
    throw new Error(`unexpected response (non-json): ${res.status}`);
  }

  const data = await res.json();

  if (!res.ok) {
    const msg = data?.detail || data?.error || `request failed: ${res.status}`;
    throw new Error(msg);
  }

  return data;
}

/* -----------------------------
   Safe Markdown → HTML
----------------------------- */
function renderMarkdown(md) {
  const src = String(md ?? "");
  if (!src.trim()) return `<div class="muted">No response.</div>`;

  const lines = src.split(/\r?\n/);
  let html = "";
  let inCode = false;
  let codeBuf = [];

  let inUl = false;
  let inOl = false;

  const inline = (s) => {
    let t = escapeHtml(s);
    t = t.replace(/`([^`]+)`/g, '<code class="inline">$1</code>');
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    return t;
  };

  const closeLists = () => {
    if (inUl) {
      html += `</ul>`;
      inUl = false;
    }
    if (inOl) {
      html += `</ol>`;
      inOl = false;
    }
  };

  const flushCode = () => {
    if (!codeBuf.length) return;
    html += `<pre class="code"><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`;
    codeBuf = [];
  };

  for (const raw of lines) {
    const line = raw ?? "";

    if (line.trim().startsWith("```")) {
      if (inCode) {
        inCode = false;
        flushCode();
      } else {
        closeLists();
        inCode = true;
      }
      continue;
    }

    if (inCode) {
      codeBuf.push(line);
      continue;
    }

    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      closeLists();
      const text = inline(h[2]);
      const tag = h[1].length <= 2 ? "h3" : "h4";
      const isSummary = (h[2] || "").toLowerCase().includes("summary");
      html += `<${tag} class="md-h ${isSummary ? "md-h-summary" : ""}">${text}</${tag}>`;
      continue;
    }

    const ol = line.match(/^\s*(\d+)\.\s+(.*)$/);
    if (ol) {
      if (inUl) {
        html += `</ul>`;
        inUl = false;
      }
      if (!inOl) {
        html += `<ol class="md-ol">`;
        inOl = true;
      }
      html += `<li>${inline(ol[2])}</li>`;
      continue;
    }

    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) {
      if (inOl) {
        html += `</ol>`;
        inOl = false;
      }
      if (!inUl) {
        html += `<ul class="md-ul">`;
        inUl = true;
      }
      html += `<li>${inline(ul[1])}</li>`;
      continue;
    }

    if (!line.trim()) {
      closeLists();
      html += `<div class="md-spacer"></div>`;
      continue;
    }

    closeLists();
    html += `<p class="md-p">${inline(line)}</p>`;
  }

  if (inCode) flushCode();
  closeLists();

  return html;
}

/* -----------------------------
   Ask QA Empty Card
----------------------------- */
function renderAskEmptyCard(mode = "idle") {
  const loading = mode === "loading";
  const title = loading ? "Generating answer…" : "QA Orchestration Chat";
  const sub = loading
    ? "Using chat history + buffer memory to produce a structured answer."
    : "Ask a QA question. Follow-ups will remember chat history.";

  const specInfo = currentSpecId
    ? `<div class="muted" style="margin-top:8px;">Linked Spec: <code>${escapeHtml(
        currentSpecId
      )}</code></div>`
    : `<div class="muted" style="margin-top:8px;">No spec linked. (Run a spec to link one.)</div>`;

  return `
    <div class="card card-answer card-empty">
      <div class="empty-wrap">
        <img class="empty-logo" src="${BRAND.ASK_LOGO}" alt="AI QA Architect Logo" />
        <div class="empty-title">${escapeHtml(title)}</div>
        <div class="empty-sub">${escapeHtml(sub)}</div>
        ${specInfo}

        <div class="think ${loading ? "is-loading" : ""}">
          <div class="think-label">${loading ? "Thinking…" : "Ready"}</div>
          <div class="think-bar" aria-hidden="true"><span></span></div>
        </div>

        ${
          loading
            ? `<div class="dots" aria-hidden="true"><span></span><span></span><span></span></div>`
            : ``
        }
      </div>
    </div>
  `;
}

/* Ask QA: render ONLY the latest assistant reply (no chat history UI) */
function renderAssistantOnlyCard(replyMd) {
  const content = `<div class="md">${renderMarkdown(replyMd)}</div>`;
  return `
    <div class="card card-answer">
      <div class="card-head">
        <div class="head-left">
          <div class="kicker">ASSISTANT</div>
        </div>
      </div>
      <div class="clamp-target">${content}</div>
    </div>
  `;
}

/* -----------------------------
   Chat rendering
----------------------------- */
function renderChatThread(messages = [], summary = "") {
  const items = (messages || []).map((m) => {
    const isUser = (m.role || "").toLowerCase() === "user";
    const roleLabel = isUser ? "You" : m.role || "assistant";
    const content = `<div class="md">${renderMarkdown(m.content)}</div>`;
    const ts = m.created_at
      ? `<div class="muted" style="margin-top:6px;font-size:12px;">${escapeHtml(
          fmtTs(m.created_at)
        )}</div>`
      : "";
    return `
      <div class="card" style="margin-bottom:10px;">
        <div class="card-head">
          <div class="head-left">
            <div class="kicker">${escapeHtml(roleLabel)}</div>
          </div>
        </div>
        <div class="clamp-target">${content}${ts}</div>
      </div>
    `;
  });

  const summaryHtml = summary
    ? `<div class="card" style="margin-bottom:12px;">
         <div class="card-head">
           <div class="head-left"><div class="kicker">Memory Summary</div></div>
         </div>
         <div class="clamp-target md">${renderMarkdown(summary)}</div>
       </div>`
    : "";

  if (!items.length) return summaryHtml + renderAskEmptyCard("idle");
  return summaryHtml + items.join("");
}

/* -----------------------------
   Dashboard (metrics)
----------------------------- */
async function loadMetrics() {
  try {
    const m = await fetchJson("/api/metrics");

    if (
      m.total_runs === undefined &&
      m.unique_suites === undefined &&
      m.total_passed === undefined &&
      m.total_failed === undefined
    ) {
      const runs = await fetchJson("/api/runs");
      const computed = computeMetricsFromRuns(runs);
      applyMetricsToUI(computed);
      renderChart(computed);
      return;
    }

    applyMetricsToUI(m);
    renderChart(m);
  } catch (e) {
    console.error(e);
  }
}

function computeMetricsFromRuns(runs) {
  const list = Array.isArray(runs) ? runs : [];
  const total_runs = list.length;

  const uniq = new Set();
  let total_passed = 0;
  let total_failed = 0;

  for (const r of list) {
    if (r?.goal) uniq.add(String(r.goal).toLowerCase());
    total_passed += Number(r?.passed || 0);
    total_failed += Number(r?.failed || 0);
  }

  const total = total_passed + total_failed;
  const average_pass_rate =
    total > 0 ? Math.round((total_passed / total) * 100) : 0;

  return {
    total_runs,
    unique_suites: uniq.size,
    total_passed,
    total_failed,
    average_pass_rate,
  };
}

function applyMetricsToUI(data) {
  document.getElementById("totalRuns").innerText =
    data.unique_suites ?? data.total_runs ?? 0;

  const extra = document.getElementById("runsExtra");
  if (extra) extra.innerText = `Executions: ${data.total_runs ?? 0}`;

  document.getElementById("passRate").innerText =
    (data.average_pass_rate ?? 0) + "%";
  document.getElementById("totalPassed").innerText = data.total_passed ?? 0;
  document.getElementById("totalFailed").innerText = data.total_failed ?? 0;
}

function renderChart(data) {
  if (typeof Chart === "undefined") return;
  const ctx = document.getElementById("metricsChart");
  if (!ctx) return;
  if (chart) chart.destroy();

  chart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Passed", "Failed"],
      datasets: [{ data: [data.total_passed ?? 0, data.total_failed ?? 0] }],
    },
    options: {
      plugins: { legend: { labels: { color: "#E2E8F0" } } },
    },
  });
}

function renderSpecThinking() {
  return `
    <div class="spec-thinking" aria-live="polite">
      <div class="label">Thinking…</div>
      <div class="bar"></div>
    </div>
  `;
}

/* -----------------------------
   Spec creation + execution
----------------------------- */
async function createSpecFromPaste(text) {
  const data = await fetchJson("/api/specs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, embed: true }),
  });
  return data.spec_id;
}

async function createSpecFromUpload(file, extraText) {
  const form = new FormData();
  if (file) form.append("file", file);
  if (extraText) form.append("text", extraText);
  form.append("embed", "1");

  const data = await fetchJson("/api/specs", {
    method: "POST",
    body: form,
  });
  return data.spec_id;
}

async function runAgentWithSpecId(spec_id) {
  const data = await fetchJson("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      spec_id,
      task_type: "generate_testcases",
      options: {},
      use_rag: true,
      html: false,
      trace: false,
    }),
  });
  return data;
}

/* -----------------------------
   Run Spec
----------------------------- */
async function runSpec() {
  const specTextEl = document.getElementById("specInput");
  const spec = (specTextEl?.value || "").trim();
  const resultDiv = document.getElementById("executionResult");

  const fileEl = document.getElementById("specFile");
  const file = fileEl?.files?.[0] || null;

  if (!spec && !file) {
    resultDiv.innerHTML = `<div class="card card-error">Please paste a user story/spec OR upload a file.</div>`;
    return;
  }

  resultDiv.innerHTML = renderSpecThinking();

  try {
    const spec_id = file
      ? await createSpecFromUpload(file, spec)
      : await createSpecFromPaste(spec);

    currentSpecId = spec_id;
    localStorage.setItem(STORE.specKey, spec_id);

    const data = await runAgentWithSpecId(spec_id);

    const artifacts = pickArtifacts(data);
    const failed = data.failed ?? 0;

    resultDiv.innerHTML = `
      <div class="card card-run">
        <div class="card-head">
          <div class="head-left">
            <div class="kicker">Execution Result</div>
            <div class="title">${escapeHtml(
              data.goal ?? "Run completed"
            )}</div>
            <div class="muted" style="margin-top:6px;">Spec ID: <code>${escapeHtml(
              spec_id
            )}</code></div>
          </div>
          <div class="${badgeClass(failed)}">${statusText(failed)}</div>
        </div>

        <div class="metrics">
          <div class="metric">
            <div class="metric-label">Passed</div>
            <div class="metric-value good">${data.passed ?? 0}</div>
          </div>
          <div class="metric">
            <div class="metric-label">Failed</div>
            <div class="metric-value bad">${data.failed ?? 0}</div>
          </div>
          <div class="metric">
            <div class="metric-label">Steps</div>
            <div class="metric-value">${data.total_steps ?? "-"}</div>
          </div>
          <div class="metric">
            <div class="metric-label">When</div>
            <div class="metric-value">${escapeHtml(
              relTime(data.timestamp)
            )}</div>
            <div class="metric-sub">${escapeHtml(fmtTs(data.timestamp))}</div>
          </div>
        </div>

        <div class="section">
          <div class="section-title">Artifacts</div>
          ${
            artifactButtons(artifacts) ||
            `<div class="muted">No artifacts generated for this run.</div>`
          }
        </div>
      </div>
    `;

    await loadMetrics();
    await initChat();
  } catch (e) {
    console.error(e);
    resultDiv.innerHTML = `<div class="card card-error">❌ ${escapeHtml(
      e.message
    )}</div>`;
  }
}

/* -----------------------------
   Chat init + history
----------------------------- */
async function initChat() {
  if (activeConversationId) return activeConversationId;

  const data = await fetchJson("/api/chat/start", { method: "POST" });
  activeConversationId = data.conversation_id;
  localStorage.setItem(STORE.convKey, activeConversationId);
  return activeConversationId;
}

async function loadChatHistory() {
  try {
    await initChat();
    const data = await fetchJson("/api/chat/history");
    const resultDiv = document.getElementById("askResult");
    resultDiv.innerHTML = renderChatThread(
      data.messages || [],
      data.summary || ""
    );
  } catch (e) {
    console.error(e);
    const resultDiv = document.getElementById("askResult");
    resultDiv.innerHTML = `<div class="card card-error">❌ ${escapeHtml(
      e.message
    )}</div>`;
  }
}

/* -----------------------------
   Ask QA (chat memory)
----------------------------- */
async function askQA() {
  const input = document.getElementById("askInput");
  const question = (input?.value || "").trim();
  const resultDiv = document.getElementById("askResult");

  if (!question) {
    if (resultDiv)
      resultDiv.innerHTML = `<div class="card card-error">Ask something first.</div>`;
    return;
  }

  if (resultDiv) resultDiv.innerHTML = renderAskEmptyCard("loading");

  try {
    await initChat();

    const payload = {
      conversation_id: activeConversationId,
      message: question,
      spec_id: currentSpecId || null,
      use_rag: true,
    };

    const res = await fetchJson("/api/chat/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const reply = res?.reply ?? res?.answer ?? "";
    if (resultDiv) resultDiv.innerHTML = renderAssistantOnlyCard(reply);

    if (input) {
      input.value = "";
      input.focus();
    }
  } catch (e) {
    console.error(e);
    if (resultDiv)
      resultDiv.innerHTML = `<div class="card card-error">❌ ${escapeHtml(
        e.message
      )}</div>`;
  }
}

/* -----------------------------
   Run History
----------------------------- */
async function loadRuns() {
  // normal page scroll behaviour for history view
  setScrollLock(false);
  hideAll();
  document.getElementById("runs").classList.remove("hidden");

  try {
    const runs = await fetchJson("/api/runs");

    const container = document.getElementById("runsList");
    container.innerHTML = "";

    const items = (runs || []).slice().reverse();
    if (!items.length) {
      container.innerHTML = `<div class="card"><div class="muted">No runs yet.</div></div>`;
      return;
    }

    for (const r of items) {
      const failed = r.failed ?? 0;
      const artifacts = pickArtifacts(r);

      container.innerHTML += `
        <div class="card card-run">
          <div class="card-head">
            <div class="head-left">
              <div class="kicker">Execution</div>
              <div class="title">${escapeHtml(r.goal ?? "Run")}</div>
            </div>
            <div class="${badgeClass(failed)}">${statusText(failed)}</div>
          </div>

          <div class="metrics" style="margin-top:10px;">
            <div class="metric">
              <div class="metric-label">Passed</div>
              <div class="metric-value good">${r.passed ?? 0}</div>
            </div>
            <div class="metric">
              <div class="metric-label">Failed</div>
              <div class="metric-value bad">${r.failed ?? 0}</div>
            </div>
            <div class="metric">
              <div class="metric-label">When</div>
              <div class="metric-value">${escapeHtml(
                relTime(r.timestamp)
              )}</div>
              <div class="metric-sub">${escapeHtml(fmtTs(r.timestamp))}</div>
            </div>
            <div class="metric">
              <div class="metric-label">Timestamp</div>
              <div class="metric-value" style="font-size:12px; font-weight:600;">
                ${escapeHtml(String(r.timestamp ?? ""))}
              </div>
            </div>
          </div>

          <div class="section" style="margin-top:12px;">
            ${
              artifactButtons(artifacts) ||
              `<div class="muted">No artifacts for this run.</div>`
            }
          </div>
        </div>
      `;
    }
  } catch (e) {
    console.error(e);
    document.getElementById("runsList").innerHTML =
      `<div class="card card-error">❌ ${escapeHtml(e.message)}</div>`;
  }
}

/* -----------------------------
   Admin UI (beautified rendering)
----------------------------- */
function adminKvRow(k, vHtml) {
  return `
    <div class="admin-kv-row">
      <div class="admin-k">${escapeHtml(k)}</div>
      <div class="admin-v">${vHtml}</div>
    </div>
  `;
}

function adminPill(text) {
  return `<span class="admin-pill">${escapeHtml(text)}</span>`;
}

async function showAdmin() {
  setScrollLock(false);
  hideAll();
  document.getElementById("admin").classList.remove("hidden");
  await loadAdmin();
}

async function loadAdmin() {
  const meDiv = document.getElementById("adminMe");
  meDiv.innerHTML = `<div class="muted">Loading…</div>`;

  // Load LLM config first
  await loadLLMConfig();

  try {
    const me = await fetchJson("/api/admin/me");

    // Current access panel
    meDiv.innerHTML =
      adminKvRow(
        "Tenant",
        `${escapeHtml(me.tenant.name)} <span class="muted">(${escapeHtml(
          me.tenant.slug
        )})</span>`
      ) +
      adminKvRow("Email", escapeHtml(me.account.email || "")) +
      adminKvRow("Tenant role", adminPill(me.tenant_role)) +
      adminKvRow("Platform role", adminPill(me.platform_role));

    // Tenant admin panels may 403
    await loadMembers();
    await loadInvites();
    await loadAudit();

    // Platform admin panels may 403
    await loadTenants();
  } catch (e) {
    meDiv.innerHTML = `<div class="card card-error">❌ ${escapeHtml(
      e.message
    )}</div>`;
  }
}

async function loadMembers() {
  const container = document.getElementById("membersList");
  container.innerHTML = `<div class="muted">Loading members…</div>`;

  try {
    const members = await fetchJson("/api/admin/members");
    if (!members.length) {
      container.innerHTML = `<div class="muted">No members.</div>`;
      return;
    }

    container.innerHTML = members
      .map(
        (m) => `
        <div class="admin-item">
          <div class="admin-item-main">
            <div class="admin-item-title">${escapeHtml(m.email)}</div>
            <div class="muted">status: ${escapeHtml(
              m.status
            )} • role: ${escapeHtml(m.role)}</div>
          </div>

          <div class="admin-item-actions">
            <select class="admin-select" id="role_${m.membership_id}">
              ${["viewer", "member", "admin", "owner"]
                .map(
                  (r) =>
                    `<option value="${r}" ${
                      r === m.role ? "selected" : ""
                    }>${r}</option>`
                )
                .join("")}
            </select>

            <select class="admin-select" id="status_${m.membership_id}">
              ${["active", "disabled"]
                .map(
                  (s) =>
                    `<option value="${s}" ${
                      s === m.status ? "selected" : ""
                    }>${s}</option>`
                )
                .join("")}
            </select>

            <button class="btn btn-primary btn-sm" onclick="saveMember('${
              m.membership_id
            }')">Save</button>
          </div>
        </div>
      `
      )
      .join("");
  } catch (e) {
    container.innerHTML = `<div class="muted">Not available: ${escapeHtml(
      e.message
    )}</div>`;
  }
}

async function saveMember(membershipId) {
  const roleEl = document.getElementById(`role_${membershipId}`);
  const statusEl = document.getElementById(`status_${membershipId}`);
  if (!roleEl || !statusEl) return;

  const role = roleEl.value;
  const status = statusEl.value;

  await fetchJson(`/api/admin/members/${membershipId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, status }),
  });

  await loadMembers();
  await loadAudit();
}

async function loadInvites() {
  const container = document.getElementById("invitesList");
  container.innerHTML = `<div class="muted">Loading invites…</div>`;

  try {
    const invites = await fetchJson("/api/admin/invites");
    if (!invites.length) {
      container.innerHTML = `<div class="muted">No invites.</div>`;
      return;
    }

    container.innerHTML = invites
      .map(
        (i) => `
        <div class="admin-item">
          <div class="admin-item-main">
            <div class="admin-item-title">${escapeHtml(i.email)}</div>
            <div class="muted">
              role: ${escapeHtml(i.role)} • expires: ${escapeHtml(
          i.expires_at
        )} • accepted: ${escapeHtml(i.accepted_at || "-")}
            </div>
          </div>
        </div>
      `
      )
      .join("");
  } catch (e) {
    container.innerHTML = `<div class="muted">Not available: ${escapeHtml(
      e.message
    )}</div>`;
  }
}

async function createInvite() {
  const email = (document.getElementById("inviteEmail").value || "").trim();
  const role = document.getElementById("inviteRole").value;
  const out = document.getElementById("inviteResult");
  out.innerHTML = "";

  if (!email) {
    out.textContent = "Enter an email.";
    return;
  }

  try {
    const res = await fetchJson("/api/admin/invite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, role }),
    });

    out.innerHTML = res.dev_token
      ? `Invited. Dev token: <code>${escapeHtml(
          res.dev_token
        )}</code>`
      : `Added member: <code>${escapeHtml(email)}</code>`;

    document.getElementById("inviteEmail").value = "";
    await loadMembers();
    await loadInvites();
    await loadAudit();
  } catch (e) {
    out.textContent = `❌ ${e.message}`;
  }
}

async function loadAudit() {
  const container = document.getElementById("auditList");
  container.innerHTML = `<div class="muted">Loading audit…</div>`;

  try {
    const logs = await fetchJson("/api/admin/audit");
    if (!logs.length) {
      container.innerHTML = `<div class="muted">No audit logs.</div>`;
      return;
    }

    container.innerHTML = logs
      .map(
        (a) => `
        <div class="admin-item">
          <div class="admin-item-main">
            <div class="admin-item-title">${escapeHtml(a.action)}</div>
            <div class="muted">${escapeHtml(a.created_at)}</div>
            <div class="muted" style="margin-top:6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size: 12px;">
              ${escapeHtml(JSON.stringify(a.meta || {}))}
            </div>
          </div>
        </div>
      `
      )
      .join("");
  } catch (e) {
    container.innerHTML = `<div class="muted">Not available: ${escapeHtml(
      e.message
    )}</div>`;
  }
}

/* Platform admin */
async function loadTenants() {
  const container = document.getElementById("tenantsList");
  container.innerHTML = `<div class="muted">Loading tenants…</div>`;

  try {
    const tenants = await fetchJson("/api/platform/tenants");
    if (!tenants.length) {
      container.innerHTML = `<div class="muted">No tenants.</div>`;
      return;
    }

    container.innerHTML = tenants
      .map(
        (t) => `
        <div class="admin-item">
          <div class="admin-item-main">
            <div class="admin-item-title">${escapeHtml(
              t.slug
            )} <span class="muted">— ${escapeHtml(t.name)}</span></div>
            <div class="muted">active: ${escapeHtml(
              String(t.is_active)
            )} • created: ${escapeHtml(t.created_at)}</div>
          </div>
        </div>
      `
      )
      .join("");
  } catch (e) {
    container.innerHTML = `<div class="muted">Platform admin not available: ${escapeHtml(
      e.message
    )}</div>`;
  }
}

async function createTenant() {
  const slug = (document.getElementById("tenantSlug").value || "").trim();
  const name = (document.getElementById("tenantName").value || "").trim();
  const out = document.getElementById("platformResult");
  out.textContent = "";

  if (!slug) {
    out.textContent = "Enter tenant slug";
    return;
  }

  try {
    await fetchJson("/api/platform/tenants", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, name }),
    });
    out.textContent = "Tenant created.";
    await loadTenants();
  } catch (e) {
    out.textContent = `❌ ${e.message}`;
  }
}

async function grantPlatformRole() {
  const email = (document.getElementById("platformEmail").value || "").trim();
  const role = document.getElementById("platformRole").value;
  const out = document.getElementById("platformResult");
  out.textContent = "";

  if (!email) {
    out.textContent = "Enter email";
    return;
  }

  try {
    await fetchJson("/api/platform/roles/grant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, role }),
    });
    out.textContent = "Role granted.";
  } catch (e) {
    out.textContent = `❌ ${e.message}`;
  }
}

/* -----------------------------
   Navigation helpers
----------------------------- */
function setScrollLock(on) {
  // This just toggles the body class; CSS controls who actually scrolls.
  if (on) document.body.classList.add("lock-scroll");
  else document.body.classList.remove("lock-scroll");
}

function showDashboard() {
  setScrollLock(false); // normal right-pane scroll
  hideAll();
  document.getElementById("dashboard").classList.remove("hidden");
  loadMetrics();
}

function showExecutor() {
  setScrollLock(false); // normal right-pane scroll
  hideAll();
  document.getElementById("executor").classList.remove("hidden");
}

function showAsk() {
  // 🔒 Ask QA uses locked layout: only answer content scrolls (via CSS)
  hideAll();
  setScrollLock(true);
  document.getElementById("ask").classList.remove("hidden");

  const resultDiv = document.getElementById("askResult");
  if (resultDiv) resultDiv.innerHTML = renderAskEmptyCard("idle");

  // Ensure backend conversation exists (memory kept server-side)
  initChat().catch(console.error);
}

function hideAll() {
  document.getElementById("dashboard").classList.add("hidden");
  document.getElementById("executor").classList.add("hidden");
  document.getElementById("runs").classList.add("hidden");
  document.getElementById("ask").classList.add("hidden");
  const adminEl = document.getElementById("admin");
  if (adminEl) adminEl.classList.add("hidden");
}

/* -----------------------------
   Boot
----------------------------- */
showDashboard();

// Upload UI helpers
(function initUploadUI() {
  const fileEl = document.getElementById("specFile");
  if (!fileEl) return;

  const nameEl = document.getElementById("specFileName");
  const clearBtn = document.getElementById("clearSpecFile");

  const update = () => {
    const f = fileEl.files && fileEl.files[0];
    if (nameEl) nameEl.textContent = f ? `Selected: ${f.name}` : "No file selected";
  };

  fileEl.addEventListener("change", update);

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      fileEl.value = "";
      update();
    });
  }

  update();
})();

/* Expose globals for onclick="..." */
window.showDashboard = showDashboard;
window.showExecutor = showExecutor;
window.showAsk = showAsk;
window.loadRuns = loadRuns;
window.runSpec = runSpec;
window.askQA = askQA;
window.clearAsk = clearAsk;

window.showAdmin = showAdmin;
window.createInvite = createInvite;
window.saveMember = saveMember;
window.createTenant = createTenant;

async function clearAsk() {
  const input = document.getElementById("askInput");
  const resultDiv = document.getElementById("askResult");

  if (input) {
    input.value = "";
    input.focus();
  }
  if (resultDiv) resultDiv.innerHTML = renderAskEmptyCard("idle");

  try {
    await fetchJson("/api/chat/clear", { method: "POST" });
  } catch (e) {
    console.error(e);
  }
}

window.grantPlatformRole = grantPlatformRole;

/* ─────────────────────────────────────────
   LLM Provider Config (Phase 3)
───────────────────────────────────────── */
let llmAllModels = {};

async function loadLLMConfig() {
  const statusDiv = document.getElementById("llmStatus");
  if (!statusDiv) return;
  statusDiv.innerHTML = "Loading LLM config...";

  try {
    const info = await fetchJson("/api/llm/info");
    llmAllModels = info.available_models || {};

    // Set provider dropdown
    const provSel = document.getElementById("llmProvider");
    if (provSel && info.current_provider) provSel.value = info.current_provider;

    // Populate models
    populateLLMModels(info.current_provider, info.current_model);

    // Show available providers
    const avail = info.available_providers || [];
    const pills = avail.map(p =>
      `<span class="admin-pill" style="background:${p === 'openai' ? '#166534' : '#581c87'};color:white;">${p === 'openai' ? '🟢' : '🟣'} ${p}</span>`
    ).join(" ");
    statusDiv.innerHTML = avail.length
      ? `Available: ${pills}`
      : `<span style="color:#ef4444;">⚠ No API keys configured. Add OPENAI_API_KEY or ANTHROPIC_API_KEY to .env</span>`;
  } catch (e) {
    statusDiv.innerHTML = `<span style="color:#ef4444;">Failed to load LLM config: ${escapeHtml(e.message)}</span>`;
  }
}

function populateLLMModels(provider, selected) {
  const sel = document.getElementById("llmModel");
  if (!sel) return;
  const models = llmAllModels[provider] || [];
  sel.innerHTML = models.length
    ? models.map(m => `<option value="${m}" ${m === selected ? 'selected' : ''}>${m}</option>`).join("")
    : '<option value="">No models available</option>';
}

function onLLMProviderChange() {
  const provider = document.getElementById("llmProvider").value;
  populateLLMModels(provider, "");
}

async function saveLLMProvider() {
  const provider = document.getElementById("llmProvider").value;
  const model = document.getElementById("llmModel").value;
  const statusDiv = document.getElementById("llmStatus");

  try {
    const res = await fetchJson("/api/settings/provider", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model: model || null }),
    });
    statusDiv.innerHTML = `<span style="color:#22c55e;">✓ Saved: ${escapeHtml(res.active_provider)} / ${escapeHtml(res.active_model)}</span>`;
    await loadLLMConfig();
  } catch (e) {
    statusDiv.innerHTML = `<span style="color:#ef4444;">Save failed: ${escapeHtml(e.message)}</span>`;
  }
}

async function testLLMConnection() {
  const provider = document.getElementById("llmProvider").value;
  const model = document.getElementById("llmModel").value;
  const el = document.getElementById("llmTestResult");
  if (!el) return;
  el.style.display = "block";
  el.style.background = "#1e3a5f";
  el.style.color = "#e0e0e0";
  el.textContent = `Testing ${provider}...`;

  try {
    const res = await fetchJson("/api/llm/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model: model || null }),
    });
    if (res.status === "ok") {
      el.style.background = "#14532d";
      el.textContent = `✅ ${res.provider} connected — ${res.model} responded: "${res.response}" (${res.tokens} tokens)`;
    } else {
      el.style.background = "#7f1d1d";
      el.textContent = `❌ ${res.provider} failed: ${res.error}`;
    }
  } catch (e) {
    el.style.background = "#7f1d1d";
    el.textContent = `❌ Error: ${e.message}`;
  }
}

window.onLLMProviderChange = onLLMProviderChange;
window.saveLLMProvider = saveLLMProvider;
window.testLLMConnection = testLLMConnection;