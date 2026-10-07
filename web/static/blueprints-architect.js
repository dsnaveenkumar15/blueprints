/* Architect pages: start a design, follow it live, answer the questions, refine. Loaded from a file (the CSP forbids
   inline script); every visitor-visible string is set with textContent. */
(function () {
  "use strict";

  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
                        body: JSON.stringify(body) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) {
            throw new Error(typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.");
          }
          return data;
        });
      });
  }

  function fail(err) {
    var p = document.getElementById("arch-error");
    if (p) { p.textContent = err.message; p.hidden = false; }
  }

  var form = document.getElementById("arch-form");
  if (form) {
    var text = document.getElementById("arch-text");
    var count = document.getElementById("arch-count");
    var update = function () { count.textContent = String(text.value.length); };
    text.addEventListener("input", update);
    document.querySelectorAll(".arch-example").forEach(function (b) {
      b.addEventListener("click", function () { text.value = b.getAttribute("data-text"); update(); text.focus(); });
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var button = form.querySelector("button[type=submit]");
      button.disabled = true;
      post("/api/architect/runs", { description: text.value, consent: document.getElementById("arch-consent").checked })
        .then(function (d) { location.href = "/architect/r/" + d.id; })
        .catch(function (err) { button.disabled = false; fail(err); });
    });
  }

  var root = document.getElementById("arch-run");
  if (!root) { return; }
  var id = root.getAttribute("data-id");

  var answers = document.getElementById("arch-answers");
  if (answers) {
    var buttons = answers.querySelectorAll("button");
    var send = function (values) {
      buttons.forEach(function (b) { b.disabled = true; });
      post("/api/architect/runs/" + id + "/answers", { answers: values })
        .then(function () { location.reload(); })
        .catch(function (err) { buttons.forEach(function (b) { b.disabled = false; }); fail(err); });
    };
    answers.addEventListener("submit", function (e) {
      e.preventDefault();
      var values = {};
      answers.querySelectorAll("input[type=radio]:checked").forEach(function (r) { values[r.name] = parseInt(r.value, 10); });
      send(values);
    });
    document.getElementById("arch-skip").addEventListener("click", function () { send({}); });
  }

  var refine = document.getElementById("arch-refine");
  if (refine) {
    refine.addEventListener("submit", function (e) {
      e.preventDefault();
      post("/api/architect/runs/" + id + "/refine", { instruction: document.getElementById("arch-instruction").value })
        .then(function (d) { location.href = "/architect/r/" + d.id; })
        .catch(fail);
    });
  }

  var status = root.getAttribute("data-status");
  if (status !== "queued" && status !== "running") { return; }
  var log = document.getElementById("arch-log");
  var totals = document.getElementById("arch-totals");
  var free = 0;

  function line(text) {
    var li = document.createElement("li");
    li.textContent = text;
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }

  function step(name, state) {
    var node = document.querySelector('[data-step="' + name + '"]');
    if (!node) { return; }
    if (state === "start") {
      node.classList.remove("is-done");
      node.classList.add("is-active");
      node.setAttribute("data-rounds", String(parseInt(node.getAttribute("data-rounds") || "0", 10) + 1));
    } else {
      node.classList.remove("is-active");
      node.classList.add("is-done");
    }
  }

  var source = new EventSource("/api/architect/runs/" + id + "/events");
  source.onmessage = function (m) {
    var e = JSON.parse(m.data), p = e.payload || {};
    if (e.type === "node") { step(p.name, p.status); if (p.status === "start") { line("> " + p.name); } }
    else if (e.type === "usage") {
      if (p.free) { free += 1; }
      totals.textContent = "paid $" + Number(p.total_usd).toFixed(4) + " of $" + totals.getAttribute("data-cap") +
        " · free calls " + free + " · " + p.total_tokens + " tokens";
      line("  " + p.model + (p.free ? " (free)" : "") + " · " + p.tokens + " tokens · $" + Number(p.usd).toFixed(4));
    }
    else if (e.type === "info" || e.type === "error") { line("  " + p.text); }
    else if (e.type === "candidate") {
      line("  candidate " + p.topology + ": " + p.status + (p.monthly_usd ? " · $" + Math.round(p.monthly_usd) + "/month" : ""));
    }
    else if (e.type === "check") { line("  check: " + p.problems + " problem(s)"); (p.first || []).forEach(function (f) { line("    - " + f); }); }
    else if (e.type === "redteam") { line("  red team: " + p.findings + " finding(s), " + p.significant + " to fix"); }
    else if (e.type === "review") { line("  review round " + p.round + ": " + (p.passed ? "passed" : "not yet") + " (" + p.attacks + " attack(s) found)"); }
    else if (e.type === "result") { line("  " + p.reason); }
  };
  source.addEventListener("end", function () { source.close(); location.reload(); });
})();
