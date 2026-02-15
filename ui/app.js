let chart;

async function loadMetrics() {
  try {
    const res = await fetch("/api/metrics");
    if (!res.ok) throw new Error(`metrics failed: ${res.status}`);
    const data = await res.json();

    document.getElementById("totalRuns").innerText = data.total_runs ?? 0;
    document.getElementById("passRate").innerText = (data.average_pass_rate ?? 0) + "%";
    document.getElementById("totalPassed").innerText = data.total_passed ?? 0;
    document.getElementById("totalFailed").innerText = data.total_failed ?? 0;

    renderChart(data);
  } catch (e) {
    console.error(e);
  }
}

function renderChart(data) {
  // If Chart.js isn't loaded, don't crash the whole dashboard.
  if (typeof Chart === "undefined") return;

  const ctx = document.getElementById("metricsChart");
  if (!ctx) return;

  if (chart) chart.destroy();

  chart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Passed", "Failed"],
      datasets: [{
        data: [data.total_passed ?? 0, data.total_failed ?? 0],
      }]
    }
  });
}

async function runSpec() {
  const spec = document.getElementById("specInput").value;
  const resultDiv = document.getElementById("executionResult");

  resultDiv.innerHTML = "Running AI execution...";

  try {
    const res = await fetch("/api/spec", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ spec })
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data?.error || `spec failed: ${res.status}`);
    }

    const statusClass = (data.failed ?? 0) > 0 ? "fail" : "success";
    const statusText = (data.failed ?? 0) > 0 ? "FAILED" : "SUCCESS";

    resultDiv.innerHTML =
      `<div class="card">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <p><strong>${data.goal ?? "Run completed"}</strong></p>
            <span class="status-badge ${statusClass}">
              ${statusText}
            </span>
          </div>

          <p style="margin-top:10px;">
            Passed: ${data.passed ?? 0} | Failed: ${data.failed ?? 0}
          </p>

          <p style="margin-top:6px; font-size:12px; opacity:0.6;">
            ${data.timestamp ?? ""}
          </p>
      </div>`;

    await loadMetrics();
  } catch (e) {
    console.error(e);
    resultDiv.innerHTML = `<div class="card fail">❌ ${e.message}</div>`;
  }
}

async function askQA() {
  const question = document.getElementById("askInput").value;
  const resultDiv = document.getElementById("askResult");

  resultDiv.innerHTML = "Thinking...";

  try {
    const res = await fetch("/api/explain", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ question })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data?.error || `explain failed: ${res.status}`);

    resultDiv.innerHTML = `<div class="card">${data.answer}</div>`;
  } catch (e) {
    console.error(e);
    resultDiv.innerHTML = `<div class="card fail">❌ ${e.message}</div>`;
  }
}

async function loadRuns() {
  hideAll();
  document.getElementById("runs").classList.remove("hidden");

  try {
    const res = await fetch("/api/runs");
    if (!res.ok) throw new Error(`runs failed: ${res.status}`);
    const runs = await res.json();

    const container = document.getElementById("runsList");
    container.innerHTML = "";

    (runs || []).slice().reverse().forEach(r => {
      const statusClass = (r.failed ?? 0) > 0 ? "fail" : "success";

      container.innerHTML += `
        <div class="card">
          <p><strong>${r.goal}</strong></p>
          <p class="${statusClass}">
            Passed: ${r.passed ?? 0} | Failed: ${r.failed ?? 0}
          </p>
          <p>${r.timestamp ?? ""}</p>
        </div>`;
    });
  } catch (e) {
    console.error(e);
  }
}

function showDashboard() {
  hideAll();
  document.getElementById("dashboard").classList.remove("hidden");
  loadMetrics();
}

function showExecutor() {
  hideAll();
  document.getElementById("executor").classList.remove("hidden");
}

function showAsk() {
  hideAll();
  document.getElementById("ask").classList.remove("hidden");
}

function hideAll() {
  document.getElementById("dashboard").classList.add("hidden");
  document.getElementById("executor").classList.add("hidden");
  document.getElementById("runs").classList.add("hidden");
  document.getElementById("ask").classList.add("hidden");
}

showDashboard();
