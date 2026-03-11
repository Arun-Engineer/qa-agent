/* ═══════════════════════════════════════════════
   AI QA Revamp v4 — revamp.js
   Profile, health checks, console, logout, audit
   ═══════════════════════════════════════════════ */

/* ─── State ─── */
var rvAutoScroll = true;
var rvConsoleCount = 0;
var rvConsoleInterval = null;
var rvUserRole = "member";

/* ─── Profile dropdown toggle ─── */
function toggleRvDropdown() {
  document.getElementById("rvDropdown").classList.toggle("hidden");
}
document.addEventListener("click", function (e) {
  var dd = document.getElementById("rvDropdown");
  var av = document.getElementById("rvNeonAvatar");
  if (dd && av && !av.contains(e.target) && !dd.contains(e.target))
    dd.classList.add("hidden");
});

/* ─── Load profile into avatar + dropdown ─── */
async function loadRvProfile() {
  try {
    var r = await fetch("/api/admin/me", { credentials: "include" });
    if (!r.ok) return;
    var d = await r.json();
    var email = (d.account && d.account.email) || d.email || "user@company.com";
    var name = email.split("@")[0];
    var fl = name.charAt(0).toUpperCase();
    var role = d.tenant_role || "member";
    var tenantName = (d.tenant && d.tenant.name) || "LOCAL";
    var tenantSlug = (d.tenant && d.tenant.slug) || "";

    rvUserRole = role;

    // Neon avatar letter
    var nav = document.getElementById("rvNeonAvatar");
    if (nav) nav.textContent = fl;

    // Dropdown fields
    var da = document.getElementById("rvDdAvatar");
    if (da) da.textContent = fl;
    var dn = document.getElementById("rvDdName");
    if (dn) dn.textContent = name;
    var de = document.getElementById("rvDdEmail");
    if (de) de.textContent = email;
    var dt = document.getElementById("rvDdTenant");
    if (dt) dt.textContent = tenantName + (tenantSlug ? " (" + tenantSlug + ")" : "");
    var dr = document.getElementById("rvDdRole");
    if (dr) dr.textContent = role;

    // Show console button for admin/owner/super_admin
    if (role === "owner" || role === "admin" || role === "super_admin") {
      var cb = document.getElementById("consoleSideBtn");
      if (cb) cb.style.display = "block";
    }
  } catch (e) {
    /* silent */
  }
}

/* ─── Autonomous LLM health check ─── */
async function rvHealthCheck() {
  var apiDot = document.getElementById("rvApiDot");
  var apiText = document.getElementById("rvApiText");
  var avatar = document.getElementById("rvNeonAvatar");
  var dashProvider = document.getElementById("dashLlmProvider");
  var dashModel = document.getElementById("dashLlmModel");
  var dashStatus = document.getElementById("dashLlmStatus");

  try {
    // Step 1: Get LLM info
    var infoRes = await fetch("/api/llm/info", { credentials: "include" });
    if (!infoRes.ok) throw new Error("info failed");
    var info = await infoRes.json();

    if (dashProvider) dashProvider.textContent = info.current_provider || "-";
    if (dashModel) dashModel.textContent = info.current_model || "-";

    // Step 2: Test actual connectivity
    var testRes = await fetch("/api/llm/test", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: info.current_provider,
        model: info.current_model || null,
      }),
    });
    var test = await testRes.json();

    if (test.status === "ok") {
      // Connected
      if (apiDot) { apiDot.classList.add("on"); apiDot.classList.remove("off"); }
      if (apiText) { apiText.textContent = "Connected"; apiText.style.color = "#22c55e"; }
      if (avatar) avatar.classList.remove("rv-status-down");
      if (dashStatus) dashStatus.innerHTML = '<span class="rv-dot on"></span> <span style="color:var(--good)">Active</span>';
      rvLogConsole("info", "Health check passed: " + (test.provider || info.current_provider) + " / " + (test.model || info.current_model));
    } else {
      throw new Error(test.error || "test failed");
    }
  } catch (e) {
    // Disconnected
    if (apiDot) { apiDot.classList.add("off"); apiDot.classList.remove("on"); }
    if (apiText) { apiText.textContent = "Disconnected"; apiText.style.color = "#ef4444"; }
    if (avatar) avatar.classList.add("rv-status-down");
    if (dashStatus) dashStatus.innerHTML = '<span class="rv-dot off"></span> <span style="color:var(--bad)">Inactive</span>';
    rvLogConsole("error", "Health check failed: " + e.message);
  }
}

/* ─── Logout ─── */
async function doLogout() {
  try {
    await fetch("/logout", { method: "GET", credentials: "include" });
  } catch (e) {}
  localStorage.clear();
  window.location.replace("/login");
}

/* ─── Export audit logs as CSV ─── */
function exportAuditLogs() {
  var list = document.getElementById("auditList");
  if (!list || !list.textContent.trim() || list.textContent.indexOf("No audit") >= 0) {
    alert("No audit logs to export.");
    return;
  }
  var items = list.querySelectorAll(".admin-item");
  var csv = "Action,Timestamp,Meta\n";
  items.forEach(function (item) {
    var title = item.querySelector(".admin-item-title");
    var muted = item.querySelectorAll(".muted");
    var action = title ? title.textContent.trim() : "";
    var ts = muted[0] ? muted[0].textContent.trim() : "";
    var meta = muted[1] ? muted[1].textContent.trim() : "";
    csv += '"' + action.replace(/"/g, '""') + '","' + ts + '","' + meta.replace(/"/g, '""') + '"\n';
  });
  var blob = new Blob([csv], { type: "text/csv" });
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = "audit_logs_" + new Date().toISOString().slice(0, 10) + ".csv";
  a.click();
  URL.revokeObjectURL(url);
}

/* ─── Console ─── */
function showConsole() {
  if (typeof setScrollLock === "function") setScrollLock(false);
  if (typeof hideAll === "function") hideAll();
  var el = document.getElementById("console");
  if (el) el.classList.remove("hidden");

  // Start polling if not already
  if (!rvConsoleInterval) {
    rvStartConsolePolling();
  }
}

function rvLogConsole(level, msg) {
  var body = document.getElementById("rvConsoleBody");
  if (!body) return;
  rvConsoleCount++;
  var now = new Date();
  var ts = String(now.getHours()).padStart(2, "0") + ":" +
           String(now.getMinutes()).padStart(2, "0") + ":" +
           String(now.getSeconds()).padStart(2, "0");
  var div = document.createElement("div");
  div.className = "rv-log";
  div.innerHTML =
    '<span class="rv-log-ts">' + ts + '</span>' +
    '<span class="rv-log-level ' + level + '">' + level.toUpperCase() + '</span>' +
    '<span class="rv-log-msg">' + msg + '</span>';
  body.appendChild(div);
  if (rvAutoScroll) body.scrollTop = body.scrollHeight;
  var st = document.getElementById("rvConsoleStatusText");
  if (st) st.textContent = "Connected — polling every 5s — " + rvConsoleCount + " events";
}

function rvClearConsole() {
  var body = document.getElementById("rvConsoleBody");
  if (body) body.innerHTML = "";
  rvConsoleCount = 0;
  rvLogConsole("info", "Console cleared");
}

function rvToggleAutoscroll() {
  rvAutoScroll = !rvAutoScroll;
  var btn = document.getElementById("rvAutoScrollBtn");
  if (btn) btn.textContent = "Auto-scroll: " + (rvAutoScroll ? "ON" : "OFF");
}

function rvStartConsolePolling() {
  rvLogConsole("info", "Console connected");
  rvLogConsole("info", "Monitoring system events...");

  rvConsoleInterval = setInterval(async function () {
    try {
      // Poll audit logs for new events
      var r = await fetch("/api/admin/audit", { credentials: "include" });
      if (r.ok) {
        // Just log that we polled (real impl would diff and show new entries)
      }
    } catch (e) {
      // silent
    }
  }, 60000); // Poll audit only once per minute when console is open

  // Also intercept console.error to show in panel
  var origError = console.error;
  console.error = function () {
    origError.apply(console, arguments);
    var msg = Array.prototype.slice.call(arguments).join(" ");
    rvLogConsole("error", msg);
  };

  var origWarn = console.warn;
  console.warn = function () {
    origWarn.apply(console, arguments);
    var msg = Array.prototype.slice.call(arguments).join(" ");
    rvLogConsole("warn", msg);
  };
}

/* ─── Ensure console is hidden when other pages show ─── */
(function patchHideAll() {
  // Wait for app.js to define hideAll, then patch it
  var origHideAll = window.hideAll;
  if (typeof origHideAll === "function") {
    window.hideAll = function () {
      origHideAll.apply(this, arguments);
      var c = document.getElementById("console");
      if (c) c.classList.add("hidden");
    };
  }

  // Also patch showPage if it exists (newer index.html uses it)
  var origShowPage = window.showPage;
  if (typeof origShowPage === "function") {
    window.showPage = function () {
      var c = document.getElementById("console");
      if (c) c.classList.add("hidden");
      return origShowPage.apply(this, arguments);
    };
  }

  // Fallback: use MutationObserver to hide console when dashboard/other pages become visible
  var pages = ["dashboard", "executor", "ask", "runs", "admin"];
  var observer = new MutationObserver(function () {
    var consoleEl = document.getElementById("console");
    if (!consoleEl || consoleEl.classList.contains("hidden")) return;
    for (var i = 0; i < pages.length; i++) {
      var pg = document.getElementById(pages[i]);
      if (pg && !pg.classList.contains("hidden")) {
        consoleEl.classList.add("hidden");
        return;
      }
    }
  });
  var mainEl = document.querySelector(".main");
  if (mainEl) observer.observe(mainEl, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
})();

/* ─── Boot ─── */
document.addEventListener("DOMContentLoaded", function () {
  // Ensure console starts hidden
  var c = document.getElementById("console");
  if (c) c.classList.add("hidden");

  loadRvProfile();
  rvHealthCheck();

  // Re-check health every 60 seconds
  // Health check runs once on boot only (no interval)
});

/* ─── Expose globals ─── */
window.toggleRvDropdown = toggleRvDropdown;
window.doLogout = doLogout;
window.exportAuditLogs = exportAuditLogs;
window.showConsole = showConsole;
window.rvClearConsole = rvClearConsole;
window.rvToggleAutoscroll = rvToggleAutoscroll;
