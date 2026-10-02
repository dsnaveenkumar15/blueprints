"use strict";
(function () {
  const SELECTS = ["latency", "risk", "data_sensitivity", "autonomy"];
  const NUMBERS = ["volume_per_day", "budget_usd_month", "task_kinds"];
  const FLAGS = ["open_ended", "parallelizable", "knowledge", "quality_critical"];

  function readProfile(v) {
    return {
      latency: v.latency, volume_per_day: parseInt(v.volume_per_day, 10), risk: v.risk,
      data_sensitivity: v.data_sensitivity, autonomy: v.autonomy, budget_usd_month: parseInt(v.budget_usd_month, 10),
      capabilities: Array.isArray(v.capabilities) ? v.capabilities : [], open_ended: Boolean(v.open_ended),
      task_kinds: parseInt(v.task_kinds, 10), parallelizable: Boolean(v.parallelizable), knowledge: Boolean(v.knowledge),
      quality_critical: Boolean(v.quality_critical),
    };
  }

  function safeId(id) { return /^[a-z][a-z0-9-]{0,40}$/.test(String(id)) ? String(id) : null; }

  if (typeof module !== "undefined" && module.exports) module.exports = {readProfile: readProfile, safeId: safeId};
  if (typeof document === "undefined") return;

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  const form = document.getElementById("finder-form");
  const describe = document.getElementById("describe");
  const out = document.getElementById("finder-result");
  if (!form || !describe || !out) return;

  function collect() {
    const values = {};
    for (const name of SELECTS.concat(NUMBERS)) values[name] = form.elements[name].value;
    for (const name of FLAGS) values[name] = form.elements[name].checked;
    values.capabilities = Array.from(form.querySelectorAll('input[name="capabilities"]:checked')).map(function (n) { return n.value; });
    return values;
  }

  function fill(fields) {
    for (const name of SELECTS.concat(NUMBERS)) if (fields[name] !== undefined) form.elements[name].value = String(fields[name]);
    for (const name of FLAGS) if (fields[name] !== undefined) form.elements[name].checked = Boolean(fields[name]);
    if (Array.isArray(fields.capabilities)) {
      for (const box of form.querySelectorAll('input[name="capabilities"]')) box.checked = fields.capabilities.indexOf(box.value) >= 0;
    }
  }

  function render(data) {
    out.replaceChildren();
    out.append(el("h2", "Suggested topology: " + data.decision.topology));
    out.append(el("h3", "Why"));
    const why = el("ol");
    for (const step of data.decision.trace) why.append(el("li", step.rule + ": " + step.effect + " (" + step.because + ")"));
    out.append(why);
    if (data.decision.mandatory.length) {
      out.append(el("h3", "Mandatory controls"));
      const list = el("ul");
      for (const m of data.decision.mandatory) list.append(el("li", m.kind + " (" + m.rule + ")"));
      out.append(list);
    }
    out.append(el("h3", "Nearest blueprints"));
    if (!data.nearest.length) out.append(el("p", "No published blueprint to compare with yet.", "muted"));
    for (const n of data.nearest) {
      const p = el("p");
      const id = safeId(n.id);
      if (id) {
        const a = el("a", n.title);
        a.href = "/b/" + id;
        p.append(a);
      } else {
        p.append(el("span", String(n.title)));
      }
      p.append(el("span", " · " + n.topology + " · distance " + n.distance, "muted"));
      out.append(p);
    }
    if (data.notes) {
      out.append(el("h3", "Adaptation notes"));
      out.append(el("p", data.notes));
      out.append(el("p", "Written by a small local model; check it against the rules above.", "muted"));
    }
  }

  async function send(url, payload) {
    try {
      return await fetch(url, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(payload)});
    } catch (e) {
      return null;
    }
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    out.replaceChildren(el("p", "Working…", "muted"));
    const response = await send("/api/finder", readProfile(collect()));
    if (!response) { out.replaceChildren(el("p", "Could not reach the server.")); return; }
    if (response.status === 422) { out.replaceChildren(el("p", "Some fields are out of range. Check the numbers.")); return; }
    if (response.status === 429) { out.replaceChildren(el("p", "Too many requests. Wait a minute.")); return; }
    if (!response.ok) { out.replaceChildren(el("p", "Something went wrong (" + response.status + ").")); return; }
    render(await response.json());
  });

  describe.addEventListener("submit", async function (event) {
    event.preventDefault();
    const status = document.getElementById("describe-status");
    status.textContent = "Reading…";
    const response = await send("/api/finder/prefill", {text: describe.elements.text.value});
    if (!response) { status.textContent = "Could not reach the server."; return; }
    if (response.status === 503) { status.textContent = "The model is busy. Fill the form by hand or retry shortly."; return; }
    if (!response.ok) { status.textContent = "Could not read that description. Fill the form by hand."; return; }
    fill((await response.json()).fields);
    status.textContent = "Fields filled. Check them, then press Find.";
  });
})();
