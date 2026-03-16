/* ═══════════════════════════════════════════════════════════
   Admin User Management v3
   - Console: "Users" button → full registry table
   - Admin: Quick-action card (email → remove/change role/disable)
   ═══════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  /* ═══════════════════════════════════════
     PART 1: Console — User Registry Table
     ═══════════════════════════════════════ */

  var usersInjected = false;

  function injectConsoleUsersBtn() {
    var consoleEl = document.getElementById("console");
    if (!consoleEl || usersInjected) return;
    usersInjected = true;

    // Create a "Users" button row + collapsible table area
    var wrap = document.createElement("div");
    wrap.id = "consoleUsersWrap";
    wrap.style.marginBottom = "16px";
    wrap.innerHTML =
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">' +
        '<div style="display:flex;align-items:center;gap:10px;">' +
          '<button class="btn btn-primary btn-sm" id="loadUsersBtn" onclick="loadConsoleUsers()" style="display:inline-flex;align-items:center;gap:6px;padding:7px 16px;">' +
            '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/></svg>' +
            'Show Registered Users' +
          '</button>' +
          '<span id="consoleUsersSummary" class="muted" style="font-size:12px;"></span>' +
        '</div>' +
        '<button class="btn btn-sm" id="hideUsersBtn" onclick="hideConsoleUsers()" style="display:none;font-size:11px;padding:4px 12px;">Hide</button>' +
      '</div>' +
      '<div id="consoleUsersTable" style="display:none;"></div>';

    // Insert before the live log
    var logWrap = consoleEl.querySelector(".rv-console-wrap");
    if (logWrap) {
      consoleEl.insertBefore(wrap, logWrap);
    } else {
      consoleEl.appendChild(wrap);
    }
  }

  function hideConsoleUsers() {
    var t = document.getElementById("consoleUsersTable");
    var b = document.getElementById("hideUsersBtn");
    if (t) t.style.display = "none";
    if (b) b.style.display = "none";
  }

  async function loadConsoleUsers() {
    var tableDiv = document.getElementById("consoleUsersTable");
    var summaryEl = document.getElementById("consoleUsersSummary");
    var hideBtn = document.getElementById("hideUsersBtn");
    if (!tableDiv) return;

    tableDiv.style.display = "block";
    if (hideBtn) hideBtn.style.display = "inline-block";
    tableDiv.innerHTML = '<div class="muted" style="padding:10px;">Loading users...</div>';

    try {
      var resp = await fetch("/api/admin/users", { credentials: "include" });
      if (!resp.ok) {
        if (resp.status === 403) { tableDiv.innerHTML = '<div style="padding:14px;text-align:center;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2" style="vertical-align:middle;margin-right:6px;"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg><span class="muted">Admin access required to view user registry.</span></div>'; return; }
        if (resp.status === 404) { tableDiv.innerHTML = '<div style="padding:14px;text-align:center;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2" style="vertical-align:middle;margin-right:6px;"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="23" y1="11" x2="17" y2="11"/></svg><span class="muted">User registry service not available. Please ensure the server is configured correctly.</span></div>'; return; }
        tableDiv.innerHTML = '<div style="padding:14px;text-align:center;"><span class="muted">Unable to load users. Please try again later.</span></div>'; return;
      }

      var data = await resp.json();
      var users = data.users || [];
      var s = data.summary || {};

      // Summary line
      if (summaryEl) {
        summaryEl.innerHTML =
          '<span style="color:var(--fg)">' + (s.total || 0) + '</span> users — ' +
          '<span style="color:#22c55e">' + ((s.by_status || {}).active || 0) + ' active</span>' +
          ((s.by_status || {}).pending ? ' · <span style="color:#f59e0b">' + s.by_status.pending + ' pending</span>' : '') +
          ((s.by_status || {}).disabled ? ' · <span style="color:#ef4444">' + s.by_status.disabled + ' disabled</span>' : '');
      }

      if (!users.length) {
        tableDiv.innerHTML = '<div style="padding:24px;text-align:center;"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="1.5" style="margin-bottom:8px;"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/></svg><div class="muted">No registered users found.</div><div class="muted" style="font-size:11px;margin-top:4px;">Users will appear here once they sign up.</div></div>';
        return;
      }

      var html =
        '<div class="card" style="padding:0;overflow:hidden;">' +
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">' +
        '<thead><tr style="background:rgba(148,163,184,0.06);">' +
          _th("Email") + _th("Role") + _th("Status") + _th("Registered") +
        '</tr></thead><tbody>';

      for (var i = 0; i < users.length; i++) {
        var u = users[i];
        html += '<tr style="border-top:1px solid rgba(148,163,184,0.08);">' +
          '<td style="padding:9px 12px;font-weight:600;">' + _esc(u.email) + '</td>' +
          '<td style="padding:9px 12px;">' + _roleBadge(u.role) + '</td>' +
          '<td style="padding:9px 12px;">' + _statusDot(u.status) + '</td>' +
          '<td style="padding:9px 12px;color:var(--muted);font-size:12px;">' + _fmtDate(u.registered_at) + '</td>' +
        '</tr>';
      }

      html += '</tbody></table></div>';
      tableDiv.innerHTML = html;

    } catch (e) {
      tableDiv.innerHTML = '<div style="padding:14px;text-align:center;"><span class="muted">Unable to load user registry. Please check your connection and try again.</span></div>';
    }
  }


  /* ═══════════════════════════════════════
     PART 2: Admin Panel — Quick Action Card
     ═══════════════════════════════════════ */

  var adminCardInjected = false;

  function injectAdminUserCard() {
    var adminEl = document.getElementById("admin");
    if (!adminEl || adminCardInjected) return;

    // Find the first admin-grid or the LLM config card to insert after
    var insertPoint = adminEl.querySelector(".admin-grid") || adminEl.querySelector("#llmConfigCard");
    if (!insertPoint) return;

    adminCardInjected = true;

    var card = document.createElement("div");
    card.className = "card admin-card";
    card.style.marginTop = "16px";
    card.style.marginBottom = "16px";
    card.innerHTML =
      '<div class="admin-card-title" style="display:flex;align-items:center;gap:8px;">' +
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><path d="M20 8l-4 4 4 4"/></svg>' +
        'User Management' +
      '</div>' +
      '<div class="muted" style="margin-bottom:12px;font-size:12px;">Paste user email to change role, disable, or remove from tenant.</div>' +
      '<div class="admin-form" style="gap:8px;">' +
        '<div class="admin-field" style="flex:2;">' +
          '<label class="admin-label" for="umEmail">User email</label>' +
          '<input id="umEmail" class="admin-input" placeholder="user@example.com" />' +
        '</div>' +
        '<div class="admin-field">' +
          '<label class="admin-label" for="umAction">Action</label>' +
          '<select id="umAction" class="admin-select" onchange="onUmActionChange()">' +
            '<option value="change_role">Change role</option>' +
            '<option value="disable">Disable</option>' +
            '<option value="enable">Enable</option>' +
            '<option value="remove">Remove</option>' +
          '</select>' +
        '</div>' +
        '<div class="admin-field" id="umRoleField">' +
          '<label class="admin-label" for="umRole">New role</label>' +
          '<select id="umRole" class="admin-select">' +
            '<option value="viewer">viewer</option>' +
            '<option value="member">member</option>' +
            '<option value="admin">admin</option>' +
            '<option value="owner">owner</option>' +
          '</select>' +
        '</div>' +
        '<div class="admin-field admin-actions">' +
          '<label class="admin-label" style="opacity:0;">Go</label>' +
          '<button class="btn btn-primary" onclick="execUserAction()">Apply</button>' +
        '</div>' +
      '</div>' +
      '<div id="umResult" class="muted" style="margin-top:10px;"></div>';

    insertPoint.parentNode.insertBefore(card, insertPoint);
  }

  function onUmActionChange() {
    var action = document.getElementById("umAction");
    var roleField = document.getElementById("umRoleField");
    if (!action || !roleField) return;
    roleField.style.display = action.value === "change_role" ? "" : "none";
  }

  async function execUserAction() {
    var emailEl = document.getElementById("umEmail");
    var actionEl = document.getElementById("umAction");
    var roleEl = document.getElementById("umRole");
    var resultEl = document.getElementById("umResult");

    var email = (emailEl ? emailEl.value : "").trim();
    var action = actionEl ? actionEl.value : "";
    var role = roleEl ? roleEl.value : "";

    if (!email) { if (resultEl) resultEl.innerHTML = '<span style="color:#ef4444;">Please enter an email.</span>'; return; }

    // Confirm dangerous actions
    if (action === "remove") {
      if (!confirm("Remove " + email + " from this tenant?\nTheir account remains but access is revoked.")) return;
    }
    if (action === "disable") {
      if (!confirm("Disable " + email + "?\nThey will not be able to login until re-enabled.")) return;
    }

    if (resultEl) resultEl.innerHTML = '<span class="muted">Processing...</span>';

    try {
      var body = { email: email, action: action };
      if (action === "change_role") body.role = role;

      var resp = await fetch("/api/admin/users/manage", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      var data = await resp.json();

      if (!resp.ok) {
        if (resultEl) resultEl.innerHTML = '<span style="color:#ef4444;">Error: ' + _esc(data.detail || "Failed") + '</span>';
        return;
      }

      // Success messages
      var msg = "";
      if (data.action === "removed") msg = '✓ Removed <strong>' + _esc(email) + '</strong> from tenant.';
      else if (data.action === "role_changed") msg = '✓ <strong>' + _esc(email) + '</strong> role changed: ' + _esc(data.old_role) + ' → <strong>' + _esc(data.new_role) + '</strong>';
      else if (data.action === "disabled") msg = '✓ <strong>' + _esc(email) + '</strong> has been disabled.';
      else if (data.action === "enabled") msg = '✓ <strong>' + _esc(email) + '</strong> has been re-enabled.';
      else msg = '✓ Done.';

      if (resultEl) resultEl.innerHTML = '<span style="color:#22c55e;">' + msg + '</span>';

      // Clear email input
      if (emailEl) emailEl.value = "";

      // Refresh members list if function exists
      if (typeof loadMembers === "function") loadMembers();

    } catch (e) {
      if (resultEl) resultEl.innerHTML = '<span style="color:#ef4444;">Error: ' + _esc(e.message) + '</span>';
    }
  }


  /* ═══════════════════════════════════════
     Helpers
     ═══════════════════════════════════════ */

  function _esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function _th(label) {
    return '<th style="padding:9px 12px;font-weight:700;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:0.5px;text-align:left;">' + label + '</th>';
  }

  function _roleBadge(role) {
    var c = "#3b82f6";
    if (role === "owner") c = "#8b5cf6";
    else if (role === "admin") c = "#f59e0b";
    else if (role === "viewer") c = "#6b7280";
    return '<span style="display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700;background:' + c + '20;color:' + c + ';">' + _esc(role) + '</span>';
  }

  function _statusDot(status) {
    var c = "#22c55e", label = "Active";
    if (status === "pending") { c = "#f59e0b"; label = "Pending"; }
    else if (status === "disabled") { c = "#ef4444"; label = "Disabled"; }
    return '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:7px;height:7px;border-radius:50%;background:' + c + ';display:inline-block;"></span>' + label + '</span>';
  }

  function _fmtDate(iso) {
    if (!iso) return "-";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" }) +
             " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    } catch (e) { return iso; }
  }


  /* ═══════════════════════════════════════
     Hook into existing navigation
     ═══════════════════════════════════════ */

  // Console: inject Users button when console opens
  var _origShowConsole = window.showConsole;
  window.showConsole = function () {
    if (typeof _origShowConsole === "function") _origShowConsole();
    injectConsoleUsersBtn();
  };

  // Admin: inject User Management card when admin opens
  var _origShowAdmin = window.showAdmin;
  window.showAdmin = function () {
    if (typeof _origShowAdmin === "function") _origShowAdmin();
    setTimeout(injectAdminUserCard, 100); // slight delay to let admin HTML render
  };

  /* Expose */
  window.loadConsoleUsers = loadConsoleUsers;
  window.hideConsoleUsers = hideConsoleUsers;
  window.execUserAction = execUserAction;
  window.onUmActionChange = onUmActionChange;
})();