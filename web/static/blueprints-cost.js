"use strict";
(function () {
  const DAYS = 30;
  const MAX_VOLUME = 100000000;

  function clamp(value, low, high, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.min(high, Math.max(low, n));
  }

  function estimate(data, volume, scale) {
    const v = Math.floor(clamp(volume, 1, MAX_VOLUME, data.volume));
    const s = clamp(scale, 0.1, 10, 1);
    let usd = 0;
    let seconds = 0;
    for (const step of data.steps) {
      const input = step.input_tokens * s;
      const output = step.output_tokens * s;
      usd += (input * step.input_per_mtok + output * step.output_per_mtok) / 1e6 * step.calls;
      seconds += (step.ttft_s + output / step.tokens_per_s) * step.calls;
    }
    const monthly = data.complete ? usd * v * DAYS : null;
    return {perRequest: usd, monthly: monthly, latency: seconds, volume: v,
            over: Boolean(data.complete && data.budget > 0 && monthly > data.budget)};
  }

  function money(x) { return "$" + x.toFixed(x < 1 ? 4 : 2); }

  function summary(r, complete) {
    if (!complete || r.monthly === null) return "incomplete: some models are not priced";
    return money(r.monthly) + " / month · " + money(r.perRequest) + " per request · ~" + r.latency.toFixed(1) + " s" +
      (r.over ? " · over budget" : "");
  }

  function sliderToVolume(x) { return Math.round(Math.pow(10, clamp(x, 0, 800, 0) / 100)); }
  function volumeToSlider(v) { return Math.round(Math.log10(clamp(v, 1, MAX_VOLUME, 1)) * 100); }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {estimate: estimate, summary: summary, sliderToVolume: sliderToVolume, volumeToSlider: volumeToSlider};
  }
  if (typeof document === "undefined") return;

  const dataNode = document.getElementById("cost-data");
  const volumeInput = document.getElementById("volume");
  const scaleInput = document.getElementById("scale");
  const out = document.getElementById("cost-live");
  if (!dataNode || !volumeInput || !scaleInput || !out) return;
  let data;
  try {
    data = JSON.parse(dataNode.textContent);
  } catch (e) {
    return;
  }

  let volume = data.volume;
  function update() {
    const r = estimate(data, volume, scaleInput.value);
    document.getElementById("volume-label").textContent = r.volume.toLocaleString("en-US") + " requests/day";
    document.getElementById("scale-label").textContent = Number(scaleInput.value).toFixed(2) + "x tokens";
    out.textContent = summary(r, data.complete);
  }
  volumeInput.value = String(volumeToSlider(data.volume));
  volumeInput.addEventListener("input", function () { volume = sliderToVolume(volumeInput.value); update(); });
  scaleInput.addEventListener("input", update);
  update();
})();
