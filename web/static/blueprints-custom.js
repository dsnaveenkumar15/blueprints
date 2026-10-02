"use strict";
(function () {
  const MAX_ROUNDS = 2;
  const SELECTS = ["latency", "risk", "data_sensitivity", "autonomy"];
  const NUMBERS = ["volume_per_day", "budget_usd_month", "task_kinds"];
  const FLAGS = ["open_ended", "parallelizable", "knowledge", "quality_critical"];

  function balancedEnd(text, start) {
    let depth = 0;
    let inString = false;
    for (let i = start; i < text.length; i++) {
      const ch = text.charAt(i);
      if (inString) {
        if (ch === "\\") i++;
        else if (ch === '"') inString = false;
      } else if (ch === '"') {
        inString = true;
      } else if (ch === "{") {
        depth++;
      } else if (ch === "}") {
        depth--;
        if (depth === 0) return i;
      }
    }
    return -1;
  }

  function extractBlueprint(text) {
    if (typeof text !== "string") return null;
    let best = null;
    let from = text.indexOf("{");
    for (let tries = 0; from >= 0 && tries < 50; tries++) {
      const end = balancedEnd(text, from);
      if (end > from) {
        let data = null;
        try {
          data = JSON.parse(text.slice(from, end + 1));
        } catch (e) {
          data = null;
        }
        if (data && typeof data === "object" && !Array.isArray(data) && Object.keys(data).length) {
          if (data.blueprint && typeof data.blueprint === "object" && !Array.isArray(data.blueprint)) return data.blueprint;
          if (!best || Object.keys(data).length > Object.keys(best).length) best = data;
        }
      }
      from = text.indexOf("{", from + 1);
    }
    return best;
  }

  if (typeof module !== "undefined" && module.exports) module.exports = {extractBlueprint: extractBlueprint};
  if (typeof document === "undefined") return;

  const form = document.getElementById("custom-form");
  const out = document.getElementById("custom-result");
  if (!form || !out) return;

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  function profile() {
    const p = {};
    for (const name of SELECTS) p[name] = form.elements[name].value;
    for (const name of NUMBERS) p[name] = parseInt(form.elements[name].value, 10);
    for (const name of FLAGS) p[name] = form.elements[name].checked;
    p.capabilities = Array.from(form.querySelectorAll('input[name="capabilities"]:checked')).map(function (n) { return n.value; });
    return p;
  }

  async function post(url, body) {
    const response = await fetch(url, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(body)});
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(response.status === 429 ? "Too many requests. Wait a minute." : "This site refused the request (" + response.status + ").");
    return data;
  }

  async function askClaude(key, model, prompt) {
    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {"x-api-key": key, "anthropic-version": "2023-06-01", "anthropic-dangerous-direct-browser-access": "true",
                "content-type": "application/json"},
      body: JSON.stringify({model: model, max_tokens: 16000, system: prompt.system, messages: [{role: "user", content: prompt.user}]}),
    });
    const message = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(message.error && message.error.message ? message.error.message : "Claude API error (" + response.status + ")");
    if (message.stop_reason === "refusal") throw new Error("Claude declined this request.");
    if (message.stop_reason === "max_tokens") throw new Error("Claude's reply was cut off before the blueprint was complete. Try again or choose a model with more room.");
    const block = (message.content || []).find(function (b) { return b.type === "text"; });
    const blueprint = block ? extractBlueprint(block.text) : null;
    if (!blueprint) throw new Error("Claude did not return a blueprint as JSON.");
    return blueprint;
  }

  function section(title, items) {
    const box = el("section");
    box.append(el("h3", title));
    const list = el("ul");
    for (const item of items) list.append(el("li", item, "mono"));
    box.append(list);
    return box;
  }

  function show(result, rounds) {
    out.replaceChildren();
    const lint = result.lint;
    out.append(el("p", (lint.ok ? "The linter passes" : "The linter fails") + " (" + lint.errors + " error(s), " + lint.warnings + " warning(s)) after " + rounds + " revision(s). This is your draft, not a published blueprint.", "badge"));
    if (lint.violations.length) out.append(section("Linter", lint.violations.map(function (v) { return v.severity + " · " + v.rule + " · " + v.path + " · " + v.message; })));
    if (result.decision) out.append(section("Why this topology", result.decision.trace.map(function (t) { return t.rule + ": " + t.effect + " (" + t.because + ")"; })));
    if (result.cost) {
      const c = result.cost;
      out.append(el("p", c.complete ? "About $" + c.monthly_usd.toFixed(2) + " per month, $" + c.per_request_usd.toFixed(4) + " per request, ~" + c.latency_s + " s." : "Cost is incomplete: some models are not priced.", "mono"));
    }
    if (result.diagram) {
      const details = el("details");
      details.append(el("summary", "Diagram source (Mermaid)"), el("pre", result.diagram, "mono"));
      out.append(details);
    }
    const json = el("details");
    json.append(el("summary", "Blueprint JSON"), el("pre", JSON.stringify(result.blueprint, null, 2), "mono"));
    out.append(json);
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const key = document.getElementById("byok-key").value.trim();
    const base = {profile: profile(), title: form.elements.title.value, notes: form.elements.notes.value};
    const model = form.elements.model.value;
    try {
      let blueprint = null;
      let result = null;
      let rounds = 0;
      for (let round = 0; round <= MAX_ROUNDS; round++) {
        out.replaceChildren(el("p", round === 0 ? "Asking Claude…" : "Asking Claude to fix " + result.lint.errors + " problem(s)…", "muted"));
        const prompt = await post("/api/custom/prompt", blueprint ? Object.assign({}, base, {blueprint: blueprint}) : base);
        if (round > 0 && prompt.round === 0) break;
        blueprint = await askClaude(key, model, prompt);
        result = await post("/api/custom/check", {profile: base.profile, title: base.title, blueprint: blueprint});
        rounds = round;
        if (result.lint.ok) break;
      }
      show(result, rounds);
    } catch (err) {
      out.replaceChildren(el("p", String(err && err.message ? err.message : err)));
    }
  });
})();
