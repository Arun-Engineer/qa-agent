// Replace the loadLLMConfig function in app.js with this version
// It hides the LLM card if user is not admin/owner

async function loadLLMConfig() {
  const card = document.getElementById("llmConfigCard");
  const statusDiv = document.getElementById("llmStatus");
  if (!card || !statusDiv) return;

  statusDiv.innerHTML = "Loading LLM config...";

  try {
    const info = await fetchJson("/api/llm/info");
    llmAllModels = info.available_models || {};

    const provSel = document.getElementById("llmProvider");
    if (provSel && info.current_provider) provSel.value = info.current_provider;

    populateLLMModels(info.current_provider, info.current_model);

    const avail = info.available_providers || [];
    const pills = avail.map(p =>
      `<span class="admin-pill" style="background:${p === 'openai' ? '#166534' : '#581c87'};color:white;">${p === 'openai' ? '🟢' : '🟣'} ${p}</span>`
    ).join(" ");
    statusDiv.innerHTML = avail.length
      ? `Available: ${pills}`
      : `<span style="color:#ef4444;">⚠ No API keys configured. Add OPENAI_API_KEY or ANTHROPIC_API_KEY to .env</span>`;

    card.style.display = "";  // show card
  } catch (e) {
    if (e.message && (e.message.includes("403") || e.message.includes("Forbidden"))) {
      // User is not admin/owner — hide the LLM card entirely
      card.style.display = "none";
    } else {
      statusDiv.innerHTML = `<span style="color:#ef4444;">Failed to load LLM config: ${escapeHtml(e.message)}</span>`;
    }
  }
}
