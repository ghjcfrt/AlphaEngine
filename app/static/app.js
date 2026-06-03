// 全局前端状态。这里不引入状态管理库，是为了让原型保持零构建、直接静态托管。
// plan 保存最近一次后端返回；lastSymbols 用于刷新行情；activeTab 控制结果面板。
const state = {
  plan: null,
  lastSymbols: [],
  activeTab: "ai",
  amountCurrency: "CNY",
  settings: null,
  isLoading: false,
};

// localStorage 只存用户草稿和最近一次结果，方便刷新页面后继续查看。
// 密钥不会从后端明文返回；前端只保存用户本次输入或遮罩状态。
const storageKeys = {
  planDraft: "alphaengine.planDraft.v1",
  planResult: "alphaengine.planResult.v1",
  settingsDraft: "alphaengine.settingsDraft.v1",
};
const savedSecretMask = "********";
const defaultOpenAiBaseUrl = "https://api.openai.com";
const defaultOpenAiModel = "gpt-5.4-mini";
const defaultAmountCurrency = "CNY";

// 图表和配置列表使用的基础色板；顺序与资产桶顺序对应。
const colors = ["#0d7c66", "#2f6fbb", "#bb7a14", "#7a5cbd", "#bf3f37", "#3d7f8f"];

// 后端返回英文枚举，前端统一映射成中文显示。
const riskLabels = {
  conservative: "保守",
  balanced: "均衡",
  growth: "成长",
  aggressive: "进取",
};
const liquidityLabels = {
  low: "低",
  medium: "中",
  high: "高",
};
const objectiveLabels = {
  capital_preservation: "保值",
  income: "收入",
  balanced: "均衡",
  growth: "成长",
};
const amountCurrencyLabels = {
  CNY: "RMB",
  USD: "USD",
  HKD: "HKD",
  EUR: "EUR",
  JPY: "JPY",
};

const fieldLabels = ["亏损承受", "长期持有", "波动接受", "成长偏好", "经验信心"];

// provider 表示服务商，family 表示接口协议。前端模型接口下拉框主要操作 family，
// 但提交给后端时需要同时提交 provider 和 family，保持旧配置兼容。
const providerToFamily = {
  openai: "gpt",
  openai_compatible: "openai_compatible",
  gemini: "gemini",
  anthropic: "claude",
  deepseek: "deepseek",
};
const familyToProvider = {
  gpt: "openai",
  openai_compatible: "openai_compatible",
  gemini: "gemini",
  claude: "anthropic",
  deepseek: "deepseek",
};
const aiProviderLabels = {
  auto: "Auto",
  openai: "OpenAI",
  openai_compatible: "OpenAI Compatible",
  gemini: "Gemini",
  anthropic: "Anthropic",
  claude: "Anthropic",
  deepseek: "DeepSeek",
  disabled: "Disabled",
  gpt: "OpenAI",
};
const aiFamilyLabels = {
  gpt: "OpenAI Responses",
  openai_compatible: "Openai Compatible",
  gemini: "Gemini GenerateContent",
  claude: "Anthropic Messages",
  deepseek: "DeepSeek Chat",
};
const familyHints = {
  gpt: { url: defaultOpenAiBaseUrl, model: defaultOpenAiModel },
  openai_compatible: {
    url: "",
    urlPlaceholder: "https://your-openai-compatible-api.example.com",
    model: "",
    modelPlaceholder: "your-model-name",
  },
  gemini: { url: "https://generativelanguage.googleapis.com", model: "gemini-2.5-flash" },
  claude: { url: "https://api.anthropic.com", model: "claude-sonnet-4-5" },
  deepseek: { url: "https://api.deepseek.com", model: "deepseek-v4.1" },
};

// AI Agent 的顺序需要与后端 AI_AGENT_LABELS 保持一致，便于配置弹窗稳定展示。
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

function readStoredJson(key) {
  // localStorage 可能被隐私模式禁用；失败时返回 null，不影响主流程。
  try {
    const value = window.localStorage.getItem(key);
    return value ? JSON.parse(value) : null;
  } catch {
    return null;
  }
}

function writeStoredJson(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 本地持久化只是体验增强，不应该因为浏览器限制阻断工作台使用。
  }
}

function removeStoredJson(key) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // 忽略不可用 storage。
  }
}

function setFormValue(fields, name, value) {
  // 恢复草稿时只写存在且非空的字段，避免旧草稿破坏新表单结构。
  const field = fields.namedItem(name);
  if (!field || value === undefined || value === null) {
    return;
  }
  field.value = value;
}

function setFormChecked(fields, name, value) {
  const field = fields.namedItem(name);
  if (!field || value === undefined || value === null) {
    return;
  }
  field.checked = Boolean(value);
}

function setRadioValue(form, name, value) {
  if (value === undefined || value === null) {
    return;
  }
  form.querySelectorAll(`[name="${name}"]`).forEach((input) => {
    input.checked = input.value === value;
  });
}

function currentAiEnabled(agentId) {
  // Agent 列表里展示“规则基线/AI 协作”标签时使用运行时状态。
  const aiKey = agentIdToAiKey[agentId] || "ai_advisor";
  return state.settings?.ai_agents?.[aiKey]?.ai_is_model_generated === true;
}

function formatAiRuntime(source) {
  // health/settings 都会返回 ai_agents；如果有该字段，优先汇总所有专业 Agent。
  if (source.ai_agents) {
    const agents = Object.values(source.ai_agents);
    const enabledAgents = agents.filter((agent) => agent.ai_is_model_generated);
    if (!enabledAgents.length) {
      return `0/${agents.length} 个模型 Agent · 未调用模型`;
    }
    const families = [
      ...new Set(enabledAgents.map((agent) => formatAiFamily(agent.ai_model_family))),
    ].join(" / ");
    return `${enabledAgents.length}/${agents.length} 个模型 Agent · ${families || "未配置"}`;
  }
  const provider = formatAiProvider(source.ai_runtime_provider || source.ai_advisor_provider || "-");
  const model = source.ai_runtime_model ? ` · ${source.ai_runtime_model}` : "";
  const mode = source.ai_is_model_generated ? "模型生成" : "未调用模型";
  return `${provider}${model} · ${mode}`;
}

function normalizedAiKey(value) {
  // 把 OpenAI-Compatible、openai compatible 等写法统一成 openai_compatible。
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replaceAll("-", "_")
    .replaceAll(" ", "_");
}

function formatAiProvider(value) {
  const key = normalizedAiKey(value);
  return aiProviderLabels[key] || value || "-";
}

function formatAiFamily(value) {
  const key = normalizedAiKey(value);
  return aiFamilyLabels[key] || value || "-";
}

function hintUrl(hints) {
  return hints.urlPlaceholder || hints.url || "";
}

function hintModel(hints) {
  return hints.modelPlaceholder || hints.model || "";
}

function displayAiBaseUrl(config, family) {
  // 兼容接口不能默认显示官方 OpenAI URL，否则用户容易误以为已经正确配置。
  const value = String(config?.openai_base_url || "").trim();
  if (family === "openai_compatible" && value === defaultOpenAiBaseUrl) {
    return "";
  }
  return value || (familyHints[family] || familyHints.gpt).url || "";
}

function displayAiModel(config, family) {
  // 兼容接口没有默认模型名，官方默认模型占位需要清空显示。
  const value = String(config?.openai_model || "").trim();
  if (family === "openai_compatible" && value === defaultOpenAiModel) {
    return "";
  }
  return value || (familyHints[family] || familyHints.gpt).model || "";
}

function modelInterfaceFromValues(provider, family) {
  // 禁用是 provider 层面的特殊状态，其它选项都以 family 作为下拉框值。
  const normalizedProvider = normalizedAiKey(provider);
  if (normalizedProvider === "disabled") {
    return normalizedProvider;
  }
  return normalizedAiKey(family) || "gpt";
}

function providerForModelInterface(interfaceType) {
  if (interfaceType === "disabled") {
    return interfaceType;
  }
  return familyToProvider[interfaceType] || "openai";
}

function familyForModelInterface(interfaceType) {
  return familyHints[interfaceType] ? interfaceType : "gpt";
}

function agentRole(agentId) {
  // 侧边栏根据当前运行配置区分模型 Agent、规则 Agent 和行情 API Agent。
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
  // 所有插入 innerHTML 的动态文本都走转义，避免用户输入破坏页面结构。
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatMoney(value, currency = defaultAmountCurrency) {
  // 金额单位只影响展示；后端不会做币种换算。
  const resolvedCurrency = normalizeCurrencyCode(currency);
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: resolvedCurrency,
      maximumFractionDigits: 2,
    }).format(value ?? 0);
  } catch {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: defaultAmountCurrency,
      maximumFractionDigits: 2,
    }).format(value ?? 0);
  }
}

function normalizeCurrencyCode(value, fallback = defaultAmountCurrency) {
  // 允许用户/旧草稿里的 RMB 写法，但内部统一使用 CNY。
  const normalized = String(value || defaultAmountCurrency)
    .trim()
    .toUpperCase();
  const alias = normalized === "RMB" ? "CNY" : normalized;
  return /^[A-Z]{3}$/.test(alias) ? alias : fallback;
}

function normalizeAmountCurrency(value) {
  const currency = normalizeCurrencyCode(value);
  return amountCurrencyLabels[currency] ? currency : defaultAmountCurrency;
}

function activeAmountCurrency() {
  return normalizeAmountCurrency(state.amountCurrency || defaultAmountCurrency);
}

function formatAmountCurrency(value) {
  const currency = normalizeAmountCurrency(value);
  return amountCurrencyLabels[currency] || currency;
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

function formatDateTime(value) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function labelFor(value, labels) {
  return labels[value] || value || "-";
}

function showToast(message) {
  // 页面只有一个 toast，连续提示时重置隐藏计时器。
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 5200);
}

function setLoading(isLoading) {
  // 生成计划时在空态、加载态、结果态之间切换。
  state.isLoading = isLoading;
  $("#emptyState").classList.toggle("hidden", isLoading || Boolean(state.plan));
  $("#loadingState").classList.toggle("hidden", !isLoading);
  $("#results").classList.toggle("hidden", isLoading || !state.plan);
  updateExportButton();
}

function updateExportButton() {
  const button = $("#exportResult");
  if (button) {
    button.disabled = state.isLoading || !state.plan;
  }
}

function buildRiskSliders() {
  // 风险问卷固定 5 道题，每道 1-5 分；output 会随滑块实时更新。
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
  // 已有持仓行来自 <template>，这样新增/恢复草稿时能复用同一份 DOM 结构。
  const template = $("#positionTemplate");
  const node = template.content.firstElementChild.cloneNode(true);
  node.querySelector('[data-field="symbol"]').value = position.symbol ?? "";
  node.querySelector('[data-field="quantity"]').value = position.quantity ?? "";
  node.querySelector('[data-field="average_cost"]').value = position.average_cost ?? "";
  node.querySelector(".remove-position").addEventListener("click", () => {
    node.remove();
    savePlanDraft();
  });
  $("#positions").appendChild(node);
}

function collectPositionDrafts() {
  // 草稿保留原始输入字符串，避免用户未填完时被 Number 转成 0。
  return [...document.querySelectorAll(".position-row")].map((row) => ({
    symbol: row.querySelector('[data-field="symbol"]').value,
    quantity: row.querySelector('[data-field="quantity"]').value,
    average_cost: row.querySelector('[data-field="average_cost"]').value,
  }));
}

function collectPositions() {
  // 提交给后端时再做数值转换，并过滤空代码或数量为 0 的行。
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

function collectPlanDraft() {
  // 表单草稿用于页面刷新恢复，不等同于提交 payload。
  const form = $("#planForm");
  const fields = form.elements;
  return {
    user_id: fields.namedItem("user_id").value,
    amount_currency: normalizeAmountCurrency(fields.namedItem("amount_currency").value),
    age: fields.namedItem("age").value,
    annual_income: fields.namedItem("annual_income").value,
    net_worth: fields.namedItem("net_worth").value,
    initial_capital: fields.namedItem("initial_capital").value,
    investment_horizon_years: fields.namedItem("investment_horizon_years").value,
    liquidity_need: form.querySelector('[name="liquidity_need"]:checked')?.value || "",
    investment_objective:
      form.querySelector('[name="investment_objective"]:checked')?.value || "",
    risk_answers: [...document.querySelectorAll(".slider-row input")].map((input) => input.value),
    symbols: fields.namedItem("symbols").value,
    current_positions: collectPositionDrafts(),
    include_acp_trace: fields.namedItem("include_acp_trace").checked,
  };
}

function savePlanDraft() {
  if (!$("#planForm")) {
    return;
  }
  writeStoredJson(storageKeys.planDraft, collectPlanDraft());
}

function restorePlanDraft() {
  // 恢复失败返回 false，启动流程会继续使用默认示例值。
  const draft = readStoredJson(storageKeys.planDraft);
  if (!draft || typeof draft !== "object") {
    return false;
  }

  const form = $("#planForm");
  const fields = form.elements;
  [
    "user_id",
    "amount_currency",
    "age",
    "annual_income",
    "net_worth",
    "initial_capital",
    "investment_horizon_years",
    "symbols",
  ].forEach((name) => setFormValue(fields, name, draft[name]));
  state.amountCurrency = normalizeAmountCurrency(fields.namedItem("amount_currency").value);
  setRadioValue(form, "liquidity_need", draft.liquidity_need);
  setRadioValue(form, "investment_objective", draft.investment_objective);
  setFormChecked(fields, "include_acp_trace", draft.include_acp_trace);
  applyRiskAnswers(draft.risk_answers);

  if (Array.isArray(draft.current_positions)) {
    $("#positions").innerHTML = "";
    draft.current_positions.forEach((position) => addPosition(position));
  }
  return true;
}

function applyRiskAnswers(values) {
  // 恢复问卷答案时同步 input.value 和旁边的 output 文本。
  if (!Array.isArray(values)) {
    return;
  }
  document.querySelectorAll(".slider-row input").forEach((input, index) => {
    if (values[index] === undefined || values[index] === null) {
      return;
    }
    input.value = values[index];
    input.previousElementSibling.value = input.value;
    input.previousElementSibling.textContent = input.value;
  });
}

function buildPayload(form) {
  // 这里构造的结构与 InvestmentPlanRequest 完全对应。
  const data = new FormData(form);
  const symbols = String(data.get("symbols") || "")
    .split(",")
    .map((symbol) => symbol.trim())
    .filter(Boolean);

  return {
    user_id: String(data.get("user_id")),
    profile: {
      age: Number(data.get("age")),
      amount_currency: normalizeAmountCurrency(data.get("amount_currency")),
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
  // 统一处理 fetch 错误：优先读取后端 JSON detail，否则退回状态文本。
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
  // 左侧状态栏的轻量刷新，不依赖设置弹窗完整数据。
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
  // 设置数据包含密钥是否已保存、各 Agent 实际模型和本地配置路径。
  try {
    state.settings = await requestJson("/api/v1/settings");
    renderSettingsState(state.settings);
  } catch (error) {
    showToast(`配置读取失败：${error.message}`);
  }
}

function renderAiAgentSettings(aiAgents) {
  // 每个 Agent 一张配置卡，支持独立模型接口、URL、模型名和 API Key。
  const container = $("#aiAgentSettings");
  container.innerHTML = aiAgentOrder
    .map((agentKey) => {
      const config = aiAgents[agentKey];
      const title = config?.label || agentKey;
      const role = aiAgentRoleNames[agentKey] || "AI";
      const mode = config?.ai_is_model_generated ? "模型生成" : "未调用模型";
      const runtimeProvider = formatAiProvider(config?.ai_runtime_provider);
      const runtime = config?.ai_runtime_model
        ? `${runtimeProvider} · ${config.ai_runtime_model}`
        : runtimeProvider || "-";
      const keyState = config?.has_openai_api_key
        ? "已保存 API Key，勾选后清除"
        : "未保存 API Key";
      // 后端不返回密钥明文；用固定遮罩表示“已有密钥但未修改”。
      const keyMask = config?.has_openai_api_key ? savedSecretMask : "";
      const keyPlaceholder = config?.has_openai_api_key
        ? "已保存，输入新 Key 可替换"
        : "留空则不修改";
      const provider = normalizedAiKey(config?.ai_advisor_provider) || "openai";
      const family = normalizedAiKey(config?.ai_model_family) || providerToFamily[provider] || "gpt";
      const modelInterface = modelInterfaceFromValues(provider, family);
      const hints = familyHints[family] || familyHints.gpt;
      const baseUrl = displayAiBaseUrl(config, family);
      const model = displayAiModel(config, family);
      return `
        <div class="ai-agent-card" data-agent-key="${agentKey}">
          <header>
            <div class="ai-agent-heading">
              <strong>${escapeHtml(title)}</strong>
              <span>${escapeHtml(role)} · ${escapeHtml(mode)} · ${escapeHtml(runtime)}</span>
            </div>
            <button class="small-button apply-ai-config" type="button" data-action="apply-ai-config" title="用此 Agent 配置覆盖其它全部 AI Agent">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M8 8h10v10H8z" />
                <path d="M6 16H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
              应用到全部
            </button>
          </header>
          <label>
            <span>模型接口类型</span>
            <select data-ai-field="model_interface">
              ${modelInterfaceOptions(modelInterface)}
            </select>
          </label>
          <input data-ai-field="ai_advisor_provider" type="hidden" value="${escapeHtml(provider)}" />
          <input data-ai-field="ai_model_family" type="hidden" value="${escapeHtml(family)}" />
          <label class="wide-field">
            <span>模型 API URL</span>
            <input data-ai-field="openai_base_url" value="${escapeHtml(baseUrl)}" placeholder="${escapeHtml(hintUrl(hints))}" />
          </label>
          <label>
            <span>模型名称</span>
            <input data-ai-field="openai_model" value="${escapeHtml(model)}" placeholder="${escapeHtml(hintModel(hints))}" />
          </label>
          <label>
            <span>模型 API Key</span>
            <input data-ai-field="openai_api_key" type="password" autocomplete="off" value="${keyMask}" data-secret-masked="${config?.has_openai_api_key ? "true" : "false"}" placeholder="${escapeHtml(keyPlaceholder)}" />
          </label>
          <label class="trace-toggle wide-field">
            <input data-ai-field="clear_openai_api_key" type="checkbox" />
            <span>${escapeHtml(keyState)}</span>
          </label>
        </div>
      `;
    })
    .join("");
  container.querySelectorAll('[data-ai-field="model_interface"]').forEach((select) => {
    // 切换接口类型后自动填入该 provider 的默认 URL/模型提示。
    select.addEventListener("change", () => applyModelInterfaceDefaults(select.closest(".ai-agent-card")));
  });
  container.querySelectorAll('[data-ai-field="openai_api_key"]').forEach((input) => {
    input.addEventListener("input", () => {
      // 用户只要改动遮罩值，就表示要提交新密钥。
      if (input.value !== savedSecretMask) {
        input.dataset.secretMasked = "false";
      }
    });
    input.addEventListener("focus", () => {
      if (isSavedSecretInput(input)) {
        input.select();
      }
    });
  });
  container.querySelectorAll('[data-action="apply-ai-config"]').forEach((button) => {
    button.addEventListener("click", () => applyAiConfigToAll(button.closest(".ai-agent-card")));
  });
}

function modelInterfaceOptions(selected) {
  return [
    ["gpt", "OpenAI Responses"],
    ["openai_compatible", "Openai Compatible"],
    ["gemini", "Gemini GenerateContent"],
    ["claude", "Anthropic Messages"],
    ["deepseek", "DeepSeek Chat"],
    ["disabled", "禁用"],
  ]
    .map(
      ([value, label]) =>
        `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`
    )
    .join("");
}

function renderSettingsState(settings) {
  // 用后端真实运行状态重绘设置表单，再叠加本地尚未保存的草稿。
  const form = $("#settingsForm");
  const fields = form.elements;
  renderAiAgentSettings(settings.ai_agents || {});
  fields.namedItem("market_data_provider").value = settings.market_data_provider;
  fields.namedItem("finnhub_api_key").value = "";
  fields.namedItem("polygon_api_key").value = "";
  fields.namedItem("alpha_vantage_api_key").value = "";
  fields.namedItem("clear_finnhub_api_key").checked = false;
  fields.namedItem("clear_polygon_api_key").checked = false;
  fields.namedItem("clear_alpha_vantage_api_key").checked = false;
  $("#finnhubKeyState").textContent = settings.has_finnhub_api_key
    ? "已保存 Finnhub API Key，勾选后清除"
    : "未保存 Finnhub API Key";
  $("#polygonKeyState").textContent = settings.has_polygon_api_key
    ? "已保存 Polygon API Key，勾选后清除"
    : "未保存 Polygon API Key";
  $("#alphaVantageKeyState").textContent = settings.has_alpha_vantage_api_key
    ? "已保存 Alpha Vantage API Key，勾选后清除"
    : "未保存 Alpha Vantage API Key";
  $("#configPath").textContent = settings.local_config_path;
  $("#providerStatus").textContent = settings.market_data_provider;
  $("#aiProviderStatus").textContent = formatAiRuntime(settings);
  restoreSettingsDraft();
}

function collectAiAgentDrafts() {
  // 草稿收集会保留未保存的新密钥，但不会把“已保存密钥”的遮罩当作新值。
  const aiAgents = {};
  document.querySelectorAll(".ai-agent-card").forEach((card) => {
    const agentKey = card.dataset.agentKey;
    const apiKeyField = aiField(card, "openai_api_key");
    aiAgents[agentKey] = {
      ai_advisor_provider: fieldValue(card, "ai_advisor_provider"),
      ai_model_family: fieldValue(card, "ai_model_family"),
      openai_base_url: fieldValue(card, "openai_base_url"),
      openai_model: fieldValue(card, "openai_model"),
      clear_openai_api_key: fieldChecked(card, "clear_openai_api_key"),
    };
    if (!isSavedSecretInput(apiKeyField)) {
      aiAgents[agentKey].openai_api_key = apiKeyField?.value || "";
    }
  });
  return aiAgents;
}

function collectSettingsDraft() {
  // 设置弹窗关闭或保存失败时，草稿能恢复用户刚刚编辑的字段。
  const form = $("#settingsForm");
  if (!form) {
    return null;
  }
  const fields = form.elements;
  return {
    market_data_provider: fields.namedItem("market_data_provider").value,
    finnhub_api_key: fields.namedItem("finnhub_api_key").value,
    polygon_api_key: fields.namedItem("polygon_api_key").value,
    alpha_vantage_api_key: fields.namedItem("alpha_vantage_api_key").value,
    clear_finnhub_api_key: fields.namedItem("clear_finnhub_api_key").checked,
    clear_polygon_api_key: fields.namedItem("clear_polygon_api_key").checked,
    clear_alpha_vantage_api_key: fields.namedItem("clear_alpha_vantage_api_key").checked,
    ai_agents: collectAiAgentDrafts(),
  };
}

function saveSettingsDraft(draft = collectSettingsDraft()) {
  if (!draft) {
    return;
  }
  writeStoredJson(storageKeys.settingsDraft, draft);
}

function restoreSettingsDraft() {
  const draft = readStoredJson(storageKeys.settingsDraft);
  applySettingsDraft(draft);
}

function applySettingsDraft(draft) {
  // 先渲染后端状态，再叠加草稿，避免旧草稿缺字段导致表单缺失。
  if (!draft || typeof draft !== "object") {
    return;
  }
  const form = $("#settingsForm");
  const fields = form.elements;

  setFormValue(fields, "market_data_provider", draft.market_data_provider);
  setFormValue(fields, "finnhub_api_key", draft.finnhub_api_key);
  setFormValue(fields, "polygon_api_key", draft.polygon_api_key);
  setFormValue(fields, "alpha_vantage_api_key", draft.alpha_vantage_api_key);
  setFormChecked(fields, "clear_finnhub_api_key", draft.clear_finnhub_api_key);
  setFormChecked(fields, "clear_polygon_api_key", draft.clear_polygon_api_key);
  setFormChecked(
    fields,
    "clear_alpha_vantage_api_key",
    draft.clear_alpha_vantage_api_key
  );

  if (!draft.ai_agents || typeof draft.ai_agents !== "object") {
    return;
  }
  document.querySelectorAll(".ai-agent-card").forEach((card) => {
    const agentDraft = draft.ai_agents[card.dataset.agentKey];
    if (!agentDraft || typeof agentDraft !== "object") {
      return;
    }
    setAiFieldValue(card, "ai_advisor_provider", agentDraft.ai_advisor_provider);
    setAiFieldValue(card, "ai_model_family", agentDraft.ai_model_family);
    syncModelInterfaceField(card);
    setAiFieldValue(card, "openai_base_url", agentDraft.openai_base_url);
    setAiFieldValue(card, "openai_model", agentDraft.openai_model);
    setAiFieldValue(card, "openai_api_key", agentDraft.openai_api_key);
    setAiFieldChecked(card, "clear_openai_api_key", agentDraft.clear_openai_api_key);
    applyFamilyHints(card);
  });
}

function collectAiCardConfig(card) {
  // “应用到全部”时从源卡片读取可复制配置；遮罩密钥不会复制成明文。
  const apiKeyField = aiField(card, "openai_api_key");
  return {
    ai_advisor_provider: fieldValue(card, "ai_advisor_provider"),
    ai_model_family: fieldValue(card, "ai_model_family"),
    openai_base_url: fieldValue(card, "openai_base_url"),
    openai_model: fieldValue(card, "openai_model"),
    openai_api_key: isSavedSecretInput(apiKeyField) ? null : apiKeyField?.value || "",
  };
}

function applyAiCardConfig(card, config) {
  // 复制配置后清除“删除密钥”勾选，避免批量操作误删其它 Agent 的密钥。
  setAiFieldValue(card, "ai_advisor_provider", config.ai_advisor_provider);
  setAiFieldValue(card, "ai_model_family", config.ai_model_family);
  syncModelInterfaceField(card);
  setAiFieldValue(card, "openai_base_url", config.openai_base_url);
  setAiFieldValue(card, "openai_model", config.openai_model);
  if (config.openai_api_key !== null) {
    setAiFieldValue(card, "openai_api_key", config.openai_api_key);
  }
  setAiFieldChecked(card, "clear_openai_api_key", false);
  applyFamilyHints(card);
}

function applyAiConfigToAll(sourceCard) {
  // 方便用户把一个模型配置快速应用到风险/配置/收益/合规/总结全部 Agent。
  if (!sourceCard) {
    return;
  }
  const config = collectAiCardConfig(sourceCard);
  document.querySelectorAll(".ai-agent-card").forEach((card) => {
    if (card !== sourceCard) {
      applyAiCardConfig(card, config);
    }
  });
  saveSettingsDraft();
  const title = sourceCard.querySelector(".ai-agent-heading strong")?.textContent || "当前 Agent";
  showToast(`已用 ${title} 的配置覆盖其它全部 AI Agent。`);
}

function setAiFieldValue(card, fieldName, value) {
  // AI 配置卡使用 data-ai-field，不走普通 form name，避免与全局字段冲突。
  const field = card.querySelector(`[data-ai-field="${fieldName}"]`);
  if (!field || value === undefined || value === null) {
    return;
  }
  field.value = value;
  if (fieldName === "openai_api_key" && value !== savedSecretMask) {
    field.dataset.secretMasked = "false";
  }
}

function setAiFieldChecked(card, fieldName, value) {
  const field = card.querySelector(`[data-ai-field="${fieldName}"]`);
  if (!field || value === undefined || value === null) {
    return;
  }
  field.checked = Boolean(value);
}

function syncModelInterfaceField(card) {
  // 隐藏 provider/family 字段变化后，同步可见的“模型接口类型”下拉框。
  setAiFieldValue(
    card,
    "model_interface",
    modelInterfaceFromValues(fieldValue(card, "ai_advisor_provider"), fieldValue(card, "ai_model_family"))
  );
}

function settingsDraftAfterSave(draft, payload) {
  // 保存成功后清理草稿里的明文密钥和 clear 标记，避免下次打开弹窗重复提交。
  const next = JSON.parse(JSON.stringify(draft || {}));
  if (payload.clear_finnhub_api_key) {
    next.finnhub_api_key = "";
  }
  if (payload.clear_polygon_api_key) {
    next.polygon_api_key = "";
  }
  if (payload.clear_alpha_vantage_api_key) {
    next.alpha_vantage_api_key = "";
  }
  next.clear_finnhub_api_key = false;
  next.clear_polygon_api_key = false;
  next.clear_alpha_vantage_api_key = false;

  Object.entries(next.ai_agents || {}).forEach(([agentKey, agentDraft]) => {
    if (payload.ai_agents?.[agentKey]?.clear_openai_api_key) {
      agentDraft.openai_api_key = "";
    }
    agentDraft.clear_openai_api_key = false;
  });
  return next;
}

async function loadAgents() {
  // 侧边栏 Agent 列表来自后端，保证描述和能力与实际运行实例一致。
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
  // 保存配置会触发后端重建运行时服务，因此成功后需要重新拉取状态和 Agent 列表。
  event.preventDefault();
  const form = event.currentTarget;
  const fields = form.elements;
  const draftBeforeSave = collectSettingsDraft();
  const payload = {
    ai_agents: collectAiAgentSettings(),
    market_data_provider: fields.namedItem("market_data_provider").value,
    clear_finnhub_api_key: fields.namedItem("clear_finnhub_api_key").checked,
    clear_polygon_api_key: fields.namedItem("clear_polygon_api_key").checked,
    clear_alpha_vantage_api_key: fields.namedItem("clear_alpha_vantage_api_key").checked,
  };
  const finnhubKey = fields.namedItem("finnhub_api_key").value.trim();
  const polygonKey = fields.namedItem("polygon_api_key").value.trim();
  const alphaVantageKey = fields.namedItem("alpha_vantage_api_key").value.trim();
  if (finnhubKey) {
    payload.finnhub_api_key = finnhubKey;
  }
  if (polygonKey) {
    payload.polygon_api_key = polygonKey;
  }
  if (alphaVantageKey) {
    payload.alpha_vantage_api_key = alphaVantageKey;
  }

  try {
    state.settings = await requestJson("/api/v1/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    saveSettingsDraft(settingsDraftAfterSave(draftBeforeSave, payload));
    renderSettingsState(state.settings);
    await loadHealth();
    await loadAgents();
    $("#settingsDialog").close();
    showToast("配置已保存并生效。");
  } catch (error) {
    saveSettingsDraft(draftBeforeSave);
    showToast(`配置保存失败：${error.message}`);
  }
}

function collectAiAgentSettings() {
  // 提交给后端的 AI 配置。空 API Key 表示不修改，clear_openai_api_key 表示删除。
  const aiAgents = {};
  document.querySelectorAll(".ai-agent-card").forEach((card) => {
    const agentKey = card.dataset.agentKey;
    const apiKeyField = aiField(card, "openai_api_key");
    const config = {
      ai_advisor_provider: fieldValue(card, "ai_advisor_provider"),
      ai_model_family: fieldValue(card, "ai_model_family"),
      openai_base_url: fieldValue(card, "openai_base_url").trim(),
      openai_model: fieldValue(card, "openai_model").trim(),
      clear_openai_api_key: fieldChecked(card, "clear_openai_api_key"),
    };
    const apiKey = apiKeyField?.value.trim() || "";
    if (apiKey) {
      if (!isSavedSecretInput(apiKeyField)) {
        config.openai_api_key = apiKey;
      }
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

function aiField(container, fieldName) {
  return container.querySelector(`[data-ai-field="${fieldName}"]`);
}

function isSavedSecretInput(input) {
  // 判断当前 password 输入框是否仍是后端密钥存在状态的遮罩。
  return input?.dataset.secretMasked === "true" && input.value === savedSecretMask;
}

async function openSettingsDialog() {
  // 打开前确保有最新 settings；已有 settings 时也重新渲染，以便叠加草稿。
  if (!state.settings) {
    await loadSettings();
  } else {
    renderSettingsState(state.settings);
  }
  $("#settingsDialog").showModal();
}

function applyFamilyHints(card) {
  // 根据模型接口类型更新 URL/模型占位和默认值。
  const family = fieldValue(card, "ai_model_family");
  const hints = familyHints[family];
  if (!hints) {
    return;
  }
  card.querySelector('[data-ai-field="openai_base_url"]').placeholder = hintUrl(hints);
  card.querySelector('[data-ai-field="openai_model"]').placeholder = hintModel(hints);
  syncEndpointDefaults(card, family, hints);
}

function syncEndpointDefaults(card, family, hints) {
  // 切换到兼容接口时清空官方 OpenAI 默认值；切回官方/其它接口时填默认值。
  const baseUrlInput = aiField(card, "openai_base_url");
  const modelInput = aiField(card, "openai_model");
  if (family === "openai_compatible") {
    if (baseUrlInput.value.trim() === defaultOpenAiBaseUrl) {
      baseUrlInput.value = "";
    }
    if (modelInput.value.trim() === defaultOpenAiModel) {
      modelInput.value = "";
    }
    return;
  }
  if (!baseUrlInput.value.trim() && hints.url) {
    baseUrlInput.value = hints.url;
  }
  if (!modelInput.value.trim() && hints.model) {
    modelInput.value = hints.model;
  }
}

function applyModelInterfaceDefaults(card) {
  // 用户只选择一个“接口类型”，这里自动拆成后端需要的 provider + family。
  const modelInterface = fieldValue(card, "model_interface");
  const family = familyForModelInterface(modelInterface);
  card.querySelector('[data-ai-field="ai_advisor_provider"]').value =
    providerForModelInterface(modelInterface);
  card.querySelector('[data-ai-field="ai_model_family"]').value = family;
  applyFamilyHints(card);
}

async function submitPlan(event) {
  // 生成计划主流程：构造 payload -> 调后端 coordinator -> 保存并渲染结果。
  event.preventDefault();
  setLoading(true);
  try {
    const payload = buildPayload(event.currentTarget);
    state.lastSymbols = payload.symbols;
    state.amountCurrency = payload.profile.amount_currency;
    state.plan = await requestJson("/api/v1/advice/plans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.activeTab = "ai";
    renderPlan();
    savePlanResult();
  } catch (error) {
    state.plan = null;
    removeStoredJson(storageKeys.planResult);
    showToast(`计划生成失败：${error.message}`);
  } finally {
    setLoading(false);
  }
}

function savePlanResult() {
  // 最近一次结果保存到 localStorage，刷新页面后仍能继续查看和导出。
  if (!state.plan) {
    removeStoredJson(storageKeys.planResult);
    return;
  }
  writeStoredJson(storageKeys.planResult, {
    plan: state.plan,
    lastSymbols: state.lastSymbols,
    activeTab: state.activeTab,
    amountCurrency: activeAmountCurrency(),
  });
}

function restorePlanResult() {
  // 页面加载时恢复最近一次计划结果；如果没有结果则保持空态/示例表单。
  const snapshot = readStoredJson(storageKeys.planResult);
  if (!snapshot || typeof snapshot !== "object" || !snapshot.plan) {
    return;
  }
  state.plan = snapshot.plan;
  state.lastSymbols = Array.isArray(snapshot.lastSymbols) ? snapshot.lastSymbols : [];
  state.activeTab = snapshot.activeTab || "ai";
  state.amountCurrency = normalizeAmountCurrency(snapshot.amountCurrency || activeAmountCurrency());
  renderPlan();
  setLoading(false);
}

function reportMoney(value, currency = activeAmountCurrency()) {
  // 导出报告里使用的金额格式化，空值显示为短横线。
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return formatMoney(Number(value), currency);
}

function reportList(items, variant = "") {
  // 导出报告的列表渲染，variant 用于区分警示、行动项、限制等视觉样式。
  const values = (items || []).filter(Boolean);
  if (!values.length) {
    return '<p class="muted">暂无。</p>';
  }
  const className = ["report-list", variant ? `report-list-${variant}` : ""]
    .filter(Boolean)
    .join(" ");
  return `<ul class="${className}">${values.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function reportTable(headers, rows, variant = "") {
  // 导出报告的表格渲染，所有单元格都转义后再插入 HTML。
  if (!rows.length) {
    return '<p class="muted">暂无数据。</p>';
  }
  const className = ["table-frame", variant ? `table-frame-${variant}` : ""]
    .filter(Boolean)
    .join(" ");
  return `
    <div class="${className}">
      <table>
        <thead>
          <tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows
            .map(
              (row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell ?? "-")}</td>`).join("")}</tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function buildPlanReportHtml(plan, input, exportedAt) {
  // 生成可独立打开的 HTML 报告，样式内联，便于用户直接保存/发送。
  const risk = plan.risk_assessment || {};
  const allocation = plan.allocation || {};
  const returns = plan.return_analysis || {};
  const compliance = plan.compliance_review || {};
  const aiReview = plan.ai_review || {};
  const amountCurrency = normalizeAmountCurrency(input.amount_currency || state.amountCurrency);
  const allocationRows = (allocation.buckets || []).map((bucket) => [
    bucket.instrument,
    `${Number(bucket.target_weight_pct || 0).toFixed(1)}%`,
    reportMoney(bucket.target_amount, amountCurrency),
    bucket.rationale || "-",
  ]);
  const projectionRows = (returns.projections || []).map((point) => [
    `${point.years} 年`,
    reportMoney(point.downside_value, amountCurrency),
    reportMoney(point.expected_value, amountCurrency),
    reportMoney(point.upside_value, amountCurrency),
  ]);
  const quoteRows = (plan.quotes || []).map((quote) => [
    quote.symbol,
    reportMoney(quote.current_price, quote.currency || "USD"),
    formatPercent(quote.change_percent),
    `${quote.source || "-"}${quote.is_realtime ? " · 实时" : ""}`,
    formatDateTime(quote.updated_at),
  ]);
  const positions = (input.current_positions || [])
    .filter((position) => position.symbol)
    .map((position) => [
      position.symbol,
      position.quantity || "0",
      position.average_cost ? reportMoney(position.average_cost, amountCurrency) : "-",
    ]);
  const riskAnswers = (input.risk_answers || []).join(" / ") || "-";

  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <title>AlphaEngine 投资计划报告</title>
    <style>
      :root {
        --text: #1d2523;
        --muted: #687570;
        --line: #d9e0dd;
        --panel: #ffffff;
        --soft: #f6f8fa;
        --accent: #0d7c66;
        --accent-strong: #075f4e;
        --blue: #2f6fbb;
        --amber: #bb7a14;
        --red: #bf3f37;
        --purple: #6f54b5;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        color: var(--text);
        background: linear-gradient(180deg, #eef8f5 0, #f6f7f9 290px);
        font-family: "Segoe UI", Arial, sans-serif;
      }
      main { max-width: 1040px; margin: 0 auto; padding: 34px 22px 52px; }
      h1, h2, h3, p { margin: 0; }
      h1 { font-size: 32px; line-height: 1.1; letter-spacing: 0; }
      h2 {
        display: flex;
        align-items: center;
        gap: 9px;
        margin-bottom: 14px;
        font-size: 19px;
      }
      h2::before {
        width: 5px;
        height: 18px;
        border-radius: 999px;
        background: var(--accent);
        content: "";
      }
      h3 { margin: 16px 0 9px; font-size: 15px; }
      section, .hero {
        margin-top: 16px;
        padding: 20px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--panel);
        box-shadow: 0 14px 36px rgba(32, 43, 39, 0.08);
      }
      .hero {
        margin-top: 0;
        border-color: rgba(13, 124, 102, 0.18);
        background: linear-gradient(135deg, #ffffff 0%, #eef8f5 64%, #edf4fb 100%);
      }
      .hero-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
      }
      .report-badge {
        padding: 6px 10px;
        border: 1px solid rgba(13, 124, 102, 0.22);
        border-radius: 999px;
        color: var(--accent-strong);
        background: #ffffff;
        font-size: 12px;
        font-weight: 800;
      }
      .muted { color: var(--muted); line-height: 1.65; }
      .notice {
        margin-top: 16px;
        padding: 13px 14px;
        border: 1px solid rgba(187, 122, 20, 0.25);
        border-left: 5px solid var(--amber);
        border-radius: 8px;
        background: #fff7e8;
        color: #6d4306;
        line-height: 1.6;
      }
      .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
      .metric {
        padding: 14px;
        border: 1px solid rgba(13, 124, 102, 0.16);
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.82);
      }
      .metric:nth-child(2) { border-color: rgba(47, 111, 187, 0.18); }
      .metric:nth-child(3) { border-color: rgba(187, 122, 20, 0.2); }
      .metric:nth-child(4) { border-color: rgba(191, 63, 55, 0.2); }
      .metric span { display: block; color: var(--muted); font-size: 12px; font-weight: 800; }
      .metric strong { display: block; margin-top: 6px; font-size: 22px; line-height: 1.15; }
      .facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
      .fact { padding: 11px 12px; border: 1px solid var(--line); border-radius: 8px; background: #f8faf9; }
      .fact span { display: block; color: var(--muted); font-size: 12px; font-weight: 800; }
      .fact strong { display: block; margin-top: 5px; line-height: 1.35; }
      .table-frame {
        overflow-x: auto;
        margin-top: 10px;
        border: 1px solid var(--line);
        border-radius: 8px;
      }
      table { width: 100%; min-width: 640px; border-collapse: collapse; background: #fff; }
      th, td { padding: 11px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
      tr:last-child td { border-bottom: 0; }
      tbody tr:nth-child(even) { background: #fafcfb; }
      th { color: var(--muted); font-size: 12px; background: #f1f5f3; text-transform: uppercase; }
      .report-list {
        display: grid;
        gap: 8px;
        margin: 0;
        padding: 0;
        list-style: none;
        line-height: 1.65;
      }
      .report-list li {
        padding: 10px 12px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #f8faf9;
      }
      .report-list-note li, .report-list-rationale li {
        border-color: rgba(187, 122, 20, 0.2);
        background: #fff7e8;
        box-shadow: inset 4px 0 0 var(--amber);
      }
      .report-list-warning li, .report-list-limitation li {
        border-color: rgba(191, 63, 55, 0.22);
        background: #fff0ee;
        box-shadow: inset 4px 0 0 var(--red);
      }
      .report-list-suitability li {
        border-color: rgba(47, 111, 187, 0.22);
        background: #edf4fb;
        box-shadow: inset 4px 0 0 var(--blue);
      }
      .report-list-guardrail li, .report-list-action li {
        border-color: rgba(13, 124, 102, 0.22);
        background: #eef8f5;
        box-shadow: inset 4px 0 0 var(--accent);
      }
      .report-list-insight li {
        border-color: rgba(47, 111, 187, 0.22);
        background: #edf4fb;
        box-shadow: inset 4px 0 0 var(--blue);
      }
      .ai-report-summary {
        display: grid;
        gap: 8px;
        margin-bottom: 14px;
        padding: 14px;
        border: 1px solid rgba(111, 84, 181, 0.2);
        border-radius: 8px;
        background: #f1edfb;
      }
      .ai-report-summary strong { color: var(--purple); }
      .two-col { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
      @media (max-width: 720px) {
        .metrics, .facts, .two-col, .hero-header { grid-template-columns: 1fr; }
        .hero-header { display: grid; }
        main { padding: 18px 12px 32px; }
        h1 { font-size: 28px; }
      }
      @media print {
        body { background: #fff; }
        main { max-width: none; padding: 0; }
        section, .hero { break-inside: avoid; box-shadow: none; }
        .report-list li { break-inside: avoid; }
      }
    </style>
  </head>
  <body>
    <main>
      <div class="hero">
        <div class="hero-header">
          <div>
            <h1>AlphaEngine 投资计划报告</h1>
            <p class="muted">导出时间：${escapeHtml(formatDateTime(exportedAt))}</p>
          </div>
          <span class="report-badge">${escapeHtml(formatAiProvider(aiReview.provider || "-"))}</span>
        </div>
        <div class="notice">本报告仅用于教育、研究和产品原型演示，不构成投资建议、收益承诺或个性化投顾服务。执行前请由具备资质的专业人员复核。</div>
        <div class="metrics">
          <div class="metric"><span>风险评分</span><strong>${escapeHtml(Number(risk.risk_score || 0).toFixed(0))}</strong></div>
          <div class="metric"><span>风险等级</span><strong>${escapeHtml(labelFor(risk.risk_level, riskLabels))}</strong></div>
          <div class="metric"><span>预期年化收益</span><strong>${escapeHtml(formatPercent(returns.expected_annual_return_pct))}</strong></div>
          <div class="metric"><span>人工复核</span><strong>${compliance.requires_human_review ? "需要" : "无需"}</strong></div>
        </div>
      </div>

      <section>
        <h2>投资人画像</h2>
        <div class="facts">
          <div class="fact"><span>用户 ID</span><strong>${escapeHtml(input.user_id || "-")}</strong></div>
          <div class="fact"><span>金额单位</span><strong>${escapeHtml(formatAmountCurrency(amountCurrency))}</strong></div>
          <div class="fact"><span>年龄</span><strong>${escapeHtml(input.age || "-")}</strong></div>
          <div class="fact"><span>年收入</span><strong>${escapeHtml(reportMoney(input.annual_income, amountCurrency))}</strong></div>
          <div class="fact"><span>净资产</span><strong>${escapeHtml(reportMoney(input.net_worth, amountCurrency))}</strong></div>
          <div class="fact"><span>初始投资本金</span><strong>${escapeHtml(reportMoney(input.initial_capital, amountCurrency))}</strong></div>
          <div class="fact"><span>投资期限</span><strong>${escapeHtml(input.investment_horizon_years || "-")} 年</strong></div>
          <div class="fact"><span>流动性需求</span><strong>${escapeHtml(labelFor(input.liquidity_need, liquidityLabels))}</strong></div>
          <div class="fact"><span>投资目标</span><strong>${escapeHtml(labelFor(input.investment_objective, objectiveLabels))}</strong></div>
          <div class="fact"><span>风险问卷</span><strong>${escapeHtml(riskAnswers)}</strong></div>
        </div>
      </section>

      <section>
        <h2>配置建议</h2>
        <p class="muted">再平衡频率：${escapeHtml(allocation.rebalance_frequency || "-")}</p>
        ${reportTable(["资产/工具", "目标权重", "目标金额", "理由"], allocationRows, "allocation")}
        <h3>配置备注</h3>
        ${reportList(allocation.notes, "note")}
      </section>

      <section>
        <h2>收益情景</h2>
        <p class="muted">预期年化波动：${escapeHtml(formatPercent(returns.expected_annual_volatility_pct))}</p>
        ${reportTable(["期限", "下行情景", "预期情景", "上行情景"], projectionRows, "projection")}
      </section>

      <section>
        <h2>行情快照</h2>
        <p class="muted">关注标的：${escapeHtml((state.lastSymbols || []).join(", ") || "-")}</p>
        ${reportTable(["代码", "价格", "涨跌", "来源", "更新时间"], quoteRows, "quotes")}
      </section>

      <section>
        <h2>已有持仓</h2>
        ${reportTable(["代码", "数量", "平均成本"], positions, "positions")}
      </section>

      <section>
        <h2>合规提醒</h2>
        <div class="two-col">
          <div><h3>警示</h3>${reportList(compliance.warnings, "warning")}</div>
          <div><h3>适当性</h3>${reportList(compliance.suitability_notes, "suitability")}</div>
        </div>
        <h3>护栏</h3>
        ${reportList(compliance.guardrails, "guardrail")}
      </section>

      <section>
        <h2>AI 解读</h2>
        <div class="ai-report-summary">
          <strong>${escapeHtml(aiReview.is_model_generated ? "模型生成" : "本地模拟")} · ${escapeHtml(formatAiProvider(aiReview.provider))}${aiReview.model ? ` · ${escapeHtml(aiReview.model)}` : ""}</strong>
          <p>${escapeHtml(aiReview.summary || "-")}</p>
        </div>
        <div class="two-col">
          <div><h3>关键洞察</h3>${reportList(aiReview.key_insights, "insight")}</div>
          <div><h3>行动项</h3>${reportList(aiReview.action_items, "action")}</div>
        </div>
        <h3>限制</h3>
        ${reportList(aiReview.limitations, "limitation")}
      </section>

      <section>
        <h2>风险评估理由</h2>
        ${reportList(risk.rationale, "rationale")}
      </section>
    </main>
  </body>
</html>`;
}

function exportPlanResult() {
  // 浏览器端直接生成 Blob 下载，不需要后端保存报告文件。
  if (!state.plan) {
    showToast("暂无可导出的结果。");
    updateExportButton();
    return;
  }

  const exportedAt = new Date().toISOString();
  const html = buildPlanReportHtml(state.plan, collectPlanDraft(), exportedAt);
  const blob = new Blob([html], {
    type: "text/html;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `alphaengine-investment-report-${exportedAt
    .replaceAll(":", "-")
    .replaceAll(".", "-")}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
  showToast("报告已导出。");
}

function renderPlan() {
  // 把后端响应拆成概览指标、图表、行情表和详情标签页。
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
  updateExportButton();
}

function renderAllocation() {
  // 资产配置用 conic-gradient 画甜甜圈，用进度条展示每个资产桶权重。
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
  // 收益情景按最大上行情景归一化，保证所有条形在同一尺度下比较。
  const projections = state.plan.return_analysis.projections;
  const amountCurrency = activeAmountCurrency();
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
          <span>${formatMoney(point.expected_value, amountCurrency)}</span>
        </div>
      `;
    })
    .join("");
}

function renderQuotes(quotes) {
  // 行情表只展示统一后的 QuoteSnapshot 字段，不关心具体 provider 原始结构。
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
  // 结果详情区使用单面板切换，避免同时渲染大量 trace/文本造成页面拥挤。
  const tabs = document.querySelector(".tabs");
  if (tabs) {
    tabs.dataset.activeTab = state.activeTab;
  }
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.activeTab);
  });
  const panel = $("#tabPanel");
  panel.dataset.tab = state.activeTab;
  if (state.activeTab === "ai") {
    const review = state.plan.ai_review;
    panel.innerHTML = `
      <div class="ai-review">
        <div class="ai-summary">
          <strong>${review.is_model_generated ? "模型生成" : "本地模拟"}</strong>
          <span>${escapeHtml(formatAiProvider(review.provider))}${review.model ? ` · ${escapeHtml(review.model)}` : ""}</span>
          <p>${escapeHtml(review.summary)}</p>
        </div>
        ${listMarkup([
          ...review.key_insights.map((item) => ({ text: `洞察：${item}`, type: "insight" })),
          ...review.action_items.map((item) => ({ text: `行动：${item}`, type: "action" })),
          ...review.limitations.map((item) => ({ text: `限制：${item}`, type: "limitation" })),
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
  // 普通详情列表统一走 detailItemMarkup，支持字符串或 {text, type} 对象。
  return `<ul class="detail-list">${items.map((item) => detailItemMarkup(item)).join("")}</ul>`;
}

function detailItemMarkup(item) {
  // type 会映射到 CSS 类，例如 insight/action/limitation。
  const text = typeof item === "object" && item !== null ? item.text : item;
  const type = typeof item === "object" && item !== null ? item.type : "";
  const className = type ? ` class="detail-${escapeHtml(type)}"` : "";
  return `<li${className}>${escapeHtml(text)}</li>`;
}

async function refreshQuotes() {
  // 只刷新行情快照，不重新跑风险、配置、收益和合规 Agent。
  if (!state.lastSymbols.length) {
    showToast("没有可刷新的关注标的。");
    return;
  }
  try {
    const quotes = await requestJson(
      `/api/v1/market/quotes?symbols=${encodeURIComponent(state.lastSymbols.join(","))}`
    );
    if (state.plan) {
      state.plan.quotes = quotes;
      savePlanResult();
    }
    renderQuotes(quotes);
  } catch (error) {
    showToast(`行情刷新失败：${error.message}`);
  }
}

function loadSample() {
  // 恢复 README 中的演示输入，方便用户一键跑通完整流程。
  const form = $("#planForm");
  const fields = form.elements;
  form.reset();
  fields.namedItem("user_id").value = "demo-user";
  fields.namedItem("amount_currency").value = defaultAmountCurrency;
  state.amountCurrency = defaultAmountCurrency;
  fields.namedItem("age").value = 32;
  fields.namedItem("annual_income").value = 300000;
  fields.namedItem("net_worth").value = 800000;
  fields.namedItem("initial_capital").value = 200000;
  fields.namedItem("investment_horizon_years").value = 8;
  fields.namedItem("symbols").value = "600519.SH,000001.SZ,AAPL,MSFT,SPY";
  fields.namedItem("include_acp_trace").checked = true;
  form.querySelector('[name="liquidity_need"][value="medium"]').checked = true;
  form.querySelector('[name="investment_objective"][value="growth"]').checked = true;
  $("#positions").innerHTML = "";
  addPosition({ symbol: "AAPL", quantity: 20, average_cost: 170 });
  applyRiskAnswers([4, 4, 3, 5, 4]);
  savePlanDraft();
}

function drawPreview() {
  // 右侧空态的 canvas 预览图，不依赖外部图片资源。
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
  // 集中绑定事件，保证 DOM 初始化完成后再挂监听器。
  $("#planForm").addEventListener("submit", submitPlan);
  $("#planForm").addEventListener("input", savePlanDraft);
  $("#planForm").addEventListener("change", (event) => {
    savePlanDraft();
    if (event.target?.name === "amount_currency") {
      state.amountCurrency = normalizeAmountCurrency(event.target.value);
      if (state.plan) {
        renderPlan();
        savePlanResult();
      }
    }
  });
  $("#addPosition").addEventListener("click", () => {
    addPosition();
    savePlanDraft();
  });
  $("#loadSample").addEventListener("click", loadSample);
  $("#exportResult").addEventListener("click", exportPlanResult);
  $("#refreshAgents").addEventListener("click", loadAgents);
  $("#refreshQuotes").addEventListener("click", refreshQuotes);
  $("#openSettings").addEventListener("click", openSettingsDialog);
  $("#closeSettings").addEventListener("click", () => $("#settingsDialog").close());
  $("#cancelSettings").addEventListener("click", () => $("#settingsDialog").close());
  $("#settingsForm").addEventListener("submit", saveSettings);
  $("#settingsForm").addEventListener("input", () => saveSettingsDraft());
  $("#settingsForm").addEventListener("change", () => saveSettingsDraft());
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      renderTabs();
      savePlanResult();
    });
  });
}

buildRiskSliders();
addPosition({ symbol: "AAPL", quantity: 20, average_cost: 170 });
restorePlanDraft();
bindEvents();
drawPreview();
restorePlanResult();
loadHealth();
// settings 加载完成后再加载 Agent 列表，因为 Agent 标签依赖 settings 中的 AI 状态。
loadSettings().then(loadAgents);
