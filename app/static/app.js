const state = {
  plan: null,
  lastSymbols: [],
  activeTab: "ai",
  settings: null,
};

const colors = ["#0d7c66", "#2f6fbb", "#bb7a14", "#7a5cbd", "#bf3f37", "#3d7f8f"];

const riskLabels = {
  conservative: "保守",
  balanced: "均衡",
  growth: "成长",
  aggressive: "进取",
};

const fieldLabels = ["亏损承受", "长期持有", "波动接受", "成长偏好", "经验信心"];
const familyHints = {
  gpt: { url: "https://api.openai.com", model: "gpt-5.4-mini" },
  openai_compatible: { url: "https://api.openai.com", model: "gpt-5.4-mini" },
  gemini: { url: "https://generativelanguage.googleapis.com", model: "gemini-2.5-flash" },
  claude: { url: "https://api.anthropic.com", model: "claude-sonnet-4-5" },
  deepseek: { url: "https://api.deepseek.com", model: "deepseek-v4.1" },
};
const aiAgentOrder = [
  "risk_assessment",
  "asset_allocation",
  "return_analysis",
  "compliance_review",
  "ai_advisor",
];
const agentIdToAiKey = {
  "risk-assessment-agent": "risk_assessment",
  "asset-allocation-agent": "asset_allocation",
  "return-analysis-agent": "return_analysis",
  "compliance-agent": "compliance_review",
  "ai-advisor-agent": "ai_advisor",
};
const aiAgentRoleNames = {
  risk_assessment: "风险画像",
  asset_allocation: "资产配置",
  return_analysis: "收益情景",
  compliance_review: "合规复核",
  ai_advisor: "总结解读",
};

const $ = (selector) => document.querySelector(selector);

function currentAiEnabled(agentId) {
  const aiKey = agentIdToAiKey[agentId] || "ai_advisor";
  return state.settings?.ai_agents?.[aiKey]?.ai_is_model_generated === true;
}

function formatAiRuntime(source) {
  if (source.ai_agents) {
    const agents = Object.values(source.ai_agents);
    const enabled = agents.filter((agent) => agent.ai_is_model_generated).length;
    const families = [...new Set(agents.map((agent) => agent.ai_model_family))].join(" / ");
    return `${enabled}/${agents.length} 个 AI Agent · ${families || "未配置"}`;
  }
  const provider = source.ai_runtime_provider || source.ai_advisor_provider || "-";
  const model = source.ai_runtime_model ? ` · ${source.ai_runtime_model}` : "";
  const mode = source.ai_is_model_generated ? "模型生成" : "规则/模拟";
  return `${provider}${model} · ${mode}`;
}

function agentRole(agentId) {
  if (agentId === "ai-advisor-agent") {
    return currentAiEnabled(agentId)
      ? { label: "总结 AI Agent", className: "model" }
      : { label: "本地解读 Agent", className: "rule" };
  }
  if (agentId === "market-data-agent") {
    return { label: "行情 API Agent", className: "market" };
  }
  return currentAiEnabled(agentId)
    ? { label: "AI协作 Agent", className: "model" }
    : { label: "规则基线 Agent", className: "rule" };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatMoney(value, currency = "USD") {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value ?? 0);
}

function formatPercent(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${Number(value).toFixed(2)}%`;
}

function formatDate(value) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 5200);
}

function setLoading(isLoading) {
  $("#emptyState").classList.toggle("hidden", isLoading || Boolean(state.plan));
  $("#loadingState").classList.toggle("hidden", !isLoading);
  $("#results").classList.toggle("hidden", isLoading || !state.plan);
}

function buildRiskSliders() {
  const container = $("#riskSliders");
  container.innerHTML = "";
  [4, 4, 3, 5, 4].forEach((value, index) => {
    const row = document.createElement("label");
    row.className = "slider-row";
    row.innerHTML = `
      <span>${fieldLabels[index]}</span>
      <output>${value}</output>
      <input type="range" min="1" max="5" step="1" value="${value}" name="risk_${index}" />
    `;
    const input = row.querySelector("input");
    const output = row.querySelector("output");
    input.addEventListener("input", () => {
      output.value = input.value;
      output.textContent = input.value;
    });
    container.appendChild(row);
  });
}

function addPosition(position = {}) {
  const template = $("#positionTemplate");
  const node = template.content.firstElementChild.cloneNode(true);
  node.querySelector('[data-field="symbol"]').value = position.symbol ?? "";
  node.querySelector('[data-field="quantity"]').value = position.quantity ?? "";
  node.querySelector('[data-field="average_cost"]').value = position.average_cost ?? "";
  node.querySelector(".remove-position").addEventListener("click", () => node.remove());
  $("#positions").appendChild(node);
}

function collectPositions() {
  return [...document.querySelectorAll(".position-row")]
    .map((row) => ({
      symbol: row.querySelector('[data-field="symbol"]').value.trim(),
      quantity: Number(row.querySelector('[data-field="quantity"]').value || 0),
      average_cost: row.querySelector('[data-field="average_cost"]').value
        ? Number(row.querySelector('[data-field="average_cost"]').value)
        : null,
    }))
    .filter((position) => position.symbol && position.quantity > 0);
}

function buildPayload(form) {
  const data = new FormData(form);
  const symbols = String(data.get("symbols") || "")
    .split(",")
    .map((symbol) => symbol.trim())
    .filter(Boolean);

  return {
    user_id: String(data.get("user_id")),
    profile: {
      age: Number(data.get("age")),
      annual_income: Number(data.get("annual_income")),
      net_worth: Number(data.get("net_worth")),
      initial_capital: Number(data.get("initial_capital")),
      investment_horizon_years: Number(data.get("investment_horizon_years")),
      liquidity_need: String(data.get("liquidity_need")),
      investment_objective: String(data.get("investment_objective")),
      risk_answers: [0, 1, 2, 3, 4].map((index) => Number(data.get(`risk_${index}`))),
      current_positions: collectPositions(),
    },
    symbols,
    include_acp_trace: data.get("include_acp_trace") === "on",
  };
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }
  return response.json();
}

async function loadHealth() {
  const dot = $("#statusDot");
  try {
    const health = await requestJson("/health");
    $("#apiStatus").textContent = health.status;
    $("#providerStatus").textContent = health.market_data_provider;
    $("#aiProviderStatus").textContent = formatAiRuntime(health);
    dot.className = "status-dot ok";
  } catch (error) {
    $("#apiStatus").textContent = "不可用";
    $("#providerStatus").textContent = "-";
    $("#aiProviderStatus").textContent = "-";
    dot.className = "status-dot error";
  }
}

async function loadSettings() {
  try {
    state.settings = await requestJson("/api/v1/settings");
    renderSettingsState(state.settings);
  } catch (error) {
    showToast(`配置读取失败：${error.message}`);
  }
}

function renderAiAgentSettings(aiAgents) {
  const container = $("#aiAgentSettings");
  container.innerHTML = aiAgentOrder
    .map((agentKey) => {
      const config = aiAgents[agentKey];
      const title = config?.label || agentKey;
      const role = aiAgentRoleNames[agentKey] || "AI";
      const mode = config?.ai_is_model_generated ? "模型生成" : "规则/模拟";
      const runtime = config?.ai_runtime_model
        ? `${config.ai_runtime_provider} · ${config.ai_runtime_model}`
        : config?.ai_runtime_provider || "-";
      const keyState = config?.has_openai_api_key
        ? "已保存 API Key，勾选后清除"
        : "未保存 API Key";
      const provider = config?.ai_advisor_provider || "auto";
      const family = config?.ai_model_family || "gpt";
      const hints = familyHints[family] || familyHints.gpt;
      return `
        <div class="ai-agent-card" data-agent-key="${agentKey}">
          <header>
            <strong>${escapeHtml(title)}</strong>
            <span>${escapeHtml(role)} · ${escapeHtml(mode)} · ${escapeHtml(runtime)}</span>
          </header>
          <label>
            <span>AI 提供方</span>
            <select data-ai-field="ai_advisor_provider">
              ${aiProviderOptions(provider)}
            </select>
          </label>
          <label>
            <span>模型接口类型</span>
            <select data-ai-field="ai_model_family">
              ${aiFamilyOptions(family)}
            </select>
          </label>
          <label class="wide-field">
            <span>模型 API URL</span>
            <input data-ai-field="openai_base_url" value="${escapeHtml(
              config?.openai_base_url || hints.url
            )}" placeholder="${escapeHtml(hints.url)}" />
          </label>
          <label>
            <span>模型名称</span>
            <input data-ai-field="openai_model" value="${escapeHtml(
              config?.openai_model || hints.model
            )}" placeholder="${escapeHtml(hints.model)}" />
          </label>
          <label>
            <span>模型 API Key</span>
            <input data-ai-field="openai_api_key" type="password" autocomplete="off" placeholder="留空则不修改" />
          </label>
          <label class="trace-toggle wide-field">
            <input data-ai-field="clear_openai_api_key" type="checkbox" />
            <span>${escapeHtml(keyState)}</span>
          </label>
        </div>
      `;
    })
    .join("");
  container.querySelectorAll('[data-ai-field="ai_model_family"]').forEach((select) => {
    select.addEventListener("change", () => applyFamilyHints(select.closest(".ai-agent-card")));
  });
}

function aiProviderOptions(selected) {
  return ["auto", "openai", "mock", "disabled"]
    .map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value}</option>`)
    .join("");
}

function aiFamilyOptions(selected) {
  return [
    ["gpt", "GPT / OpenAI Responses"],
    ["openai_compatible", "OpenAI Chat 兼容"],
    ["gemini", "Gemini"],
    ["claude", "Claude / Anthropic"],
    ["deepseek", "DeepSeek"],
  ]
    .map(
      ([value, label]) =>
        `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`
    )
    .join("");
}

function renderSettingsState(settings) {
  const form = $("#settingsForm");
  const fields = form.elements;
  renderAiAgentSettings(settings.ai_agents || {});
  fields.namedItem("market_data_provider").value = settings.market_data_provider;
  fields.namedItem("finnhub_api_key").value = "";
  fields.namedItem("polygon_api_key").value = "";
  fields.namedItem("clear_finnhub_api_key").checked = false;
  fields.namedItem("clear_polygon_api_key").checked = false;
  $("#finnhubKeyState").textContent = settings.has_finnhub_api_key
    ? "已保存 Finnhub API Key，勾选后清除"
    : "未保存 Finnhub API Key";
  $("#polygonKeyState").textContent = settings.has_polygon_api_key
    ? "已保存 Polygon API Key，勾选后清除"
    : "未保存 Polygon API Key";
  $("#configPath").textContent = settings.local_config_path;
  $("#providerStatus").textContent = settings.market_data_provider;
  $("#aiProviderStatus").textContent = formatAiRuntime(settings);
}

async function loadAgents() {
  const container = $("#agentList");
  container.innerHTML = '<div class="agent-item"><span>加载中...</span></div>';
  try {
    const agents = await requestJson("/api/v1/agents");
    container.innerHTML = agents
      .map((agent) => {
        const role = agentRole(agent.agent_id);
        return `
          <div class="agent-item">
            <div class="agent-title">
              <strong>${escapeHtml(agent.agent_id)}</strong>
              <em class="role-badge ${role.className}">${role.label}</em>
            </div>
            <span>${escapeHtml(agent.description)}</span>
          </div>
        `;
      })
      .join("");
  } catch (error) {
    container.innerHTML = '<div class="agent-item"><span>Agent 信息加载失败</span></div>';
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fields = form.elements;
  const payload = {
    ai_agents: collectAiAgentSettings(),
    market_data_provider: fields.namedItem("market_data_provider").value,
    clear_finnhub_api_key: fields.namedItem("clear_finnhub_api_key").checked,
    clear_polygon_api_key: fields.namedItem("clear_polygon_api_key").checked,
  };
  const finnhubKey = fields.namedItem("finnhub_api_key").value.trim();
  const polygonKey = fields.namedItem("polygon_api_key").value.trim();
  if (finnhubKey) {
    payload.finnhub_api_key = finnhubKey;
  }
  if (polygonKey) {
    payload.polygon_api_key = polygonKey;
  }

  try {
    state.settings = await requestJson("/api/v1/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderSettingsState(state.settings);
    await loadHealth();
    await loadAgents();
    $("#settingsDialog").close();
    showToast("配置已保存并生效。");
  } catch (error) {
    showToast(`配置保存失败：${error.message}`);
  }
}

function collectAiAgentSettings() {
  const aiAgents = {};
  document.querySelectorAll(".ai-agent-card").forEach((card) => {
    const agentKey = card.dataset.agentKey;
    const config = {
      ai_advisor_provider: fieldValue(card, "ai_advisor_provider"),
      ai_model_family: fieldValue(card, "ai_model_family"),
      openai_base_url: fieldValue(card, "openai_base_url").trim(),
      openai_model: fieldValue(card, "openai_model").trim(),
      clear_openai_api_key: fieldChecked(card, "clear_openai_api_key"),
    };
    const apiKey = fieldValue(card, "openai_api_key").trim();
    if (apiKey) {
      config.openai_api_key = apiKey;
    }
    aiAgents[agentKey] = config;
  });
  return aiAgents;
}

function fieldValue(container, fieldName) {
  return container.querySelector(`[data-ai-field="${fieldName}"]`)?.value || "";
}

function fieldChecked(container, fieldName) {
  return container.querySelector(`[data-ai-field="${fieldName}"]`)?.checked || false;
}

async function openSettingsDialog() {
  if (!state.settings) {
    await loadSettings();
  } else {
    renderSettingsState(state.settings);
  }
  $("#settingsDialog").showModal();
}

function applyFamilyHints(card) {
  const family = fieldValue(card, "ai_model_family");
  const hints = familyHints[family];
  if (!hints) {
    return;
  }
  card.querySelector('[data-ai-field="openai_base_url"]').placeholder = hints.url;
  card.querySelector('[data-ai-field="openai_model"]').placeholder = hints.model;
}

async function submitPlan(event) {
  event.preventDefault();
  setLoading(true);
  try {
    const payload = buildPayload(event.currentTarget);
    state.lastSymbols = payload.symbols;
    state.plan = await requestJson("/api/v1/advice/plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.activeTab = "ai";
    renderPlan();
  } catch (error) {
    state.plan = null;
    showToast(`计划生成失败：${error.message}`);
  } finally {
    setLoading(false);
  }
}

function renderPlan() {
  if (!state.plan) {
    return;
  }
  const { risk_assessment: risk, return_analysis: returns, compliance_review: review } = state.plan;
  $("#riskScore").textContent = Number(risk.risk_score).toFixed(0);
  $("#riskLevel").textContent = `${riskLabels[risk.risk_level] || risk.risk_level}风险`;
  $("#expectedReturn").textContent = formatPercent(returns.expected_annual_return_pct);
  $("#expectedVolatility").textContent = formatPercent(returns.expected_annual_volatility_pct);
  $("#reviewFlag").textContent = review.requires_human_review ? "需要" : "无需";
  $("#rebalanceText").textContent = state.plan.allocation.rebalance_frequency;
  renderAllocation();
  renderProjection();
  renderQuotes(state.plan.quotes);
  renderTabs();
}

function renderAllocation() {
  const buckets = state.plan.allocation.buckets;
  let cursor = 0;
  const segments = buckets.map((bucket, index) => {
    const start = cursor;
    cursor += bucket.target_weight_pct;
    return `${colors[index % colors.length]} ${start}% ${cursor}%`;
  });
  $("#allocationDonut").style.background = `conic-gradient(${segments.join(",")})`;
  $("#allocationList").innerHTML = buckets
    .map(
      (bucket, index) => `
        <div class="allocation-item">
          <strong>${escapeHtml(bucket.instrument)}</strong>
          <div class="allocation-bar">
            <span style="width:${bucket.target_weight_pct}%;background:${colors[index % colors.length]}"></span>
          </div>
          <span>${bucket.target_weight_pct.toFixed(1)}%</span>
        </div>
      `
    )
    .join("");
}

function renderProjection() {
  const projections = state.plan.return_analysis.projections;
  const maxValue = Math.max(...projections.flatMap((item) => [item.upside_value, item.expected_value]));
  $("#projectionChart").innerHTML = projections
    .map((point) => {
      const downside = Math.max(4, (point.downside_value / maxValue) * 100);
      const expected = Math.max(4, (point.expected_value / maxValue) * 100);
      const upside = Math.max(4, (point.upside_value / maxValue) * 100);
      return `
        <div class="projection-row">
          <strong>${point.years} 年</strong>
          <div class="projection-bars" aria-label="${point.years} 年收益情景">
            <div class="projection-line downside"><span style="width:${downside}%"></span></div>
            <div class="projection-line expected"><span style="width:${expected}%"></span></div>
            <div class="projection-line upside"><span style="width:${upside}%"></span></div>
          </div>
          <span>${formatMoney(point.expected_value)}</span>
        </div>
      `;
    })
    .join("");
}

function renderQuotes(quotes) {
  $("#quoteRows").innerHTML = quotes
    .map((quote) => {
      const change = quote.change_percent ?? quote.change;
      const direction = Number(change) >= 0 ? "positive" : "negative";
      return `
        <tr>
          <td><strong>${escapeHtml(quote.symbol)}</strong></td>
          <td>${formatMoney(quote.current_price, quote.currency || "USD")}</td>
          <td class="${direction}">${formatPercent(quote.change_percent)}</td>
          <td>${escapeHtml(quote.source)}${quote.is_realtime ? " · 实时" : ""}</td>
          <td>${formatDate(quote.updated_at)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.activeTab);
  });
  const panel = $("#tabPanel");
  if (state.activeTab === "ai") {
    const review = state.plan.ai_review;
    panel.innerHTML = `
      <div class="ai-review">
        <div class="ai-summary">
          <strong>${review.is_model_generated ? "模型生成" : "本地模拟"}</strong>
          <span>${escapeHtml(review.provider)}${review.model ? ` · ${escapeHtml(review.model)}` : ""}</span>
          <p>${escapeHtml(review.summary)}</p>
        </div>
        ${listMarkup([
          ...review.key_insights.map((item) => `洞察：${item}`),
          ...review.action_items.map((item) => `行动：${item}`),
          ...review.limitations.map((item) => `限制：${item}`),
        ])}
      </div>
    `;
    return;
  }
  if (state.activeTab === "compliance") {
    const review = state.plan.compliance_review;
    panel.innerHTML = listMarkup([
      ...review.warnings.map((item) => `警示：${item}`),
      ...review.suitability_notes.map((item) => `适当性：${item}`),
      ...review.guardrails.map((item) => `护栏：${item}`),
    ]);
    return;
  }
  if (state.activeTab === "rationale") {
    panel.innerHTML = listMarkup([
      ...state.plan.risk_assessment.rationale,
      ...state.plan.allocation.buckets.map(
        (bucket) => `${bucket.instrument} ${bucket.target_weight_pct}%：${bucket.rationale}`
      ),
      ...state.plan.allocation.notes,
    ]);
    return;
  }
  const trace = state.plan.acp_trace || [];
  panel.innerHTML = trace.length
    ? trace
        .map(
          (item) => `
            <div class="trace-item">
              <strong>${escapeHtml(item.sender)} → ${escapeHtml(item.receiver)}</strong>
              <span>${escapeHtml(item.action)} · ${formatDate(item.created_at)}</span>
            </div>
          `
        )
        .join("")
    : '<ul class="detail-list"><li>本次请求没有返回 trace。</li></ul>';
}

function listMarkup(items) {
  return `<ul class="detail-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

async function refreshQuotes() {
  if (!state.lastSymbols.length) {
    showToast("没有可刷新的关注标的。");
    return;
  }
  try {
    const quotes = await requestJson(
      `/api/v1/market/quotes?symbols=${encodeURIComponent(state.lastSymbols.join(","))}`
    );
    renderQuotes(quotes);
  } catch (error) {
    showToast(`行情刷新失败：${error.message}`);
  }
}

function loadSample() {
  const form = $("#planForm");
  const fields = form.elements;
  form.reset();
  fields.namedItem("user_id").value = "demo-user";
  fields.namedItem("age").value = 32;
  fields.namedItem("annual_income").value = 300000;
  fields.namedItem("net_worth").value = 800000;
  fields.namedItem("initial_capital").value = 200000;
  fields.namedItem("investment_horizon_years").value = 8;
  fields.namedItem("symbols").value = "600519.SH,000001.SZ,AAPL,MSFT,SPY";
  form.querySelector('[name="liquidity_need"][value="medium"]').checked = true;
  form.querySelector('[name="investment_objective"][value="growth"]').checked = true;
  $("#positions").innerHTML = "";
  addPosition({ symbol: "AAPL", quantity: 20, average_cost: 170 });
  document.querySelectorAll(".slider-row input").forEach((input, index) => {
    input.value = [4, 4, 3, 5, 4][index];
    input.previousElementSibling.textContent = input.value;
  });
}

function drawPreview() {
  const canvas = $("#previewCanvas");
  const context = canvas.getContext("2d");
  const { width, height } = canvas;
  context.clearRect(0, 0, width, height);

  const gradient = context.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "#e2f3ee");
  gradient.addColorStop(1, "#e7eef8");
  context.fillStyle = gradient;
  context.fillRect(0, 0, width, height);

  context.strokeStyle = "rgba(13, 124, 102, 0.14)";
  context.lineWidth = 1;
  for (let x = 40; x < width; x += 80) {
    context.beginPath();
    context.moveTo(x, 34);
    context.lineTo(x, height - 34);
    context.stroke();
  }
  for (let y = 48; y < height; y += 58) {
    context.beginPath();
    context.moveTo(36, y);
    context.lineTo(width - 36, y);
    context.stroke();
  }

  const points = [
    [54, 250],
    [150, 222],
    [244, 235],
    [338, 176],
    [432, 192],
    [526, 126],
    [648, 104],
  ];
  context.lineWidth = 5;
  context.strokeStyle = "#0d7c66";
  context.beginPath();
  points.forEach(([x, y], index) => {
    if (index === 0) {
      context.moveTo(x, y);
    } else {
      context.lineTo(x, y);
    }
  });
  context.stroke();

  points.forEach(([x, y]) => {
    context.fillStyle = "#ffffff";
    context.beginPath();
    context.arc(x, y, 8, 0, Math.PI * 2);
    context.fill();
    context.strokeStyle = "#0d7c66";
    context.lineWidth = 3;
    context.stroke();
  });

  context.fillStyle = "#1d2523";
  context.font = "700 28px Inter, sans-serif";
  context.fillText("Portfolio Signal", 48, 74);
  context.fillStyle = "#697571";
  context.font = "500 16px Inter, sans-serif";
  context.fillText("risk · allocation · quote · compliance", 48, 102);
}

function bindEvents() {
  $("#planForm").addEventListener("submit", submitPlan);
  $("#addPosition").addEventListener("click", () => addPosition());
  $("#loadSample").addEventListener("click", loadSample);
  $("#refreshAgents").addEventListener("click", loadAgents);
  $("#refreshQuotes").addEventListener("click", refreshQuotes);
  $("#openSettings").addEventListener("click", openSettingsDialog);
  $("#closeSettings").addEventListener("click", () => $("#settingsDialog").close());
  $("#cancelSettings").addEventListener("click", () => $("#settingsDialog").close());
  $("#settingsForm").addEventListener("submit", saveSettings);
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      renderTabs();
    });
  });
}

buildRiskSliders();
addPosition({ symbol: "AAPL", quantity: 20, average_cost: 170 });
bindEvents();
drawPreview();
loadHealth();
loadSettings().then(loadAgents);
