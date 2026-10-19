// 画面の入出力。計算は model.js に任せる。
import { dealInputs, priceAboveAverage, priceCurve, probability } from "./model.js";

const percent = (value, digits = 1) => `${(value * 100).toFixed(digits)}%`;
const oku = (value) => `${Number(value.toFixed(1)).toLocaleString("ja-JP")}億円`;
const byId = (id) => document.getElementById(id);

function readDeal() {
  return {
    price: Number(byId("price").value),
    targetNetAssets: Number(byId("target-net-assets").value),
    acquirerAssets: Number(byId("acquirer-assets").value),
    equityRatio: Number(byId("equity-ratio").value) / 100,
    relatedness: document.querySelector('input[name="relatedness"]:checked').value,
  };
}

function fillSiteNumbers(site) {
  const groups = site.holdout.groups;
  const top = groups[groups.length - 1];
  const values = {
    n: site.n.toLocaleString("ja-JP"),
    auc: site.holdout.auc.toFixed(2),
    "top-mean": percent(top.mean_probability, 0),
    "top-rate": percent(top.rate, 0),
    "holdout-n": site.holdout.n.toLocaleString("ja-JP"),
  };
  for (const element of document.querySelectorAll("[data-site]")) {
    element.textContent = values[element.dataset.site];
  }
  byId("base-rate").textContent = percent(site.base_rate);
  renderTrack(groups);
}

function renderTrack(groups) {
  const highest = Math.max(...groups.map((group) => group.rate));
  const labels = ["最も安全な20%", "", "", "", "最も危ない20%"];
  const bars = groups.map((group, i) => {
    const bar = document.createElement("div");
    const tone = i === 0 ? "safe" : i === groups.length - 1 ? "risk" : "";
    bar.className = `bar ${tone}`;
    const value = document.createElement("span");
    value.className = "bar-value";
    value.textContent = percent(group.rate);
    const fill = document.createElement("span");
    fill.className = "bar-fill";
    fill.style.height = `${(group.rate / highest) * 100}px`;
    const label = document.createElement("span");
    label.className = "bar-label";
    label.textContent = labels[i] || `${i + 1}番目`;
    bar.append(value, fill, label);
    return bar;
  });
  byId("track-bars").replaceChildren(...bars);
}

function renderResult(site, deal) {
  const error = byId("error");
  const output = byId("probability");
  try {
    const inputs = dealInputs(deal, site.max_goodwill_ratio);
    const p = probability(site.model, inputs);
    error.hidden = true;
    output.textContent = percent(p);
    output.className = `probability ${p > site.base_rate ? "risk" : "safe"}`;
    const ratio = p / site.base_rate;
    byId("comparison").textContent =
      p > site.base_rate
        ? `過去の平均（${percent(site.base_rate)}）の約${ratio.toFixed(1)}倍です。`
        : `過去の平均（${percent(site.base_rate)}）より低い見積もりです。`;
    byId("goodwill-ratio").textContent = percent(inputs.goodwill_ratio, 0);
    byId("size-ratio").textContent = percent(Math.exp(inputs.log_size_ratio));
  } catch (problem) {
    error.textContent = problem.message;
    error.hidden = false;
    output.textContent = "—";
    output.className = "probability";
    byId("comparison").textContent = "";
    byId("goodwill-ratio").textContent = "—";
    byId("size-ratio").textContent = "—";
  }
}

const SVG = "http://www.w3.org/2000/svg";

function svgElement(name, attributes, text) {
  const element = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

function niceStep(range, target) {
  const raw = range / target;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * magnitude).find((step) => step >= raw);
}

function renderCurve(site, deal) {
  const svg = byId("curve");
  svg.replaceChildren();
  const low = Math.max(deal.targetNetAssets * 1.01, deal.price * 0.2, 0.01);
  const high = Math.max(deal.price * 2.2, low * 3);
  const prices = Array.from({ length: 200 }, (_, i) => low + ((high - low) * i) / 199);
  const points = priceCurve(site, deal, prices);
  const summary = byId("chart-summary");
  if (points.length < 2) {
    summary.textContent = "この数字では、曲線を描ける買収額の範囲がありません。";
    return;
  }
  const width = 640;
  const height = 300;
  const margin = { top: 16, right: 24, bottom: 36, left: 52 };
  const xMin = points[0].price;
  const xMax = points[points.length - 1].price;
  const probabilities = points.map((point) => point.probability).concat(site.base_rate);
  const yMin = Math.max(0, Math.min(...probabilities) - 0.02);
  const yMax = Math.min(1, Math.max(...probabilities) + 0.02);
  const x = (value) => margin.left + ((value - xMin) / (xMax - xMin)) * (width - margin.left - margin.right);
  const y = (value) => height - margin.bottom - ((value - yMin) / (yMax - yMin)) * (height - margin.top - margin.bottom);

  const yStep = niceStep(yMax - yMin, 5);
  for (let tick = Math.ceil(yMin / yStep) * yStep; tick <= yMax; tick += yStep) {
    svg.append(svgElement("line", { class: "grid", x1: margin.left, x2: width - margin.right, y1: y(tick), y2: y(tick) }));
    svg.append(svgElement("text", { class: "tick", x: margin.left - 8, y: y(tick) + 4, "text-anchor": "end" }, percent(tick, 0)));
  }
  const xStep = niceStep(xMax - xMin, 6);
  for (let tick = Math.ceil(xMin / xStep) * xStep; tick <= xMax; tick += xStep) {
    svg.append(svgElement("text", { class: "tick", x: x(tick), y: height - margin.bottom + 18, "text-anchor": "middle" }, oku(tick)));
  }
  svg.append(svgElement("line", { class: "axis", x1: margin.left, x2: width - margin.right, y1: height - margin.bottom, y2: height - margin.bottom }));

  svg.append(svgElement("line", { class: "average", x1: margin.left, x2: width - margin.right, y1: y(site.base_rate), y2: y(site.base_rate) }));
  svg.append(svgElement("text", { class: "label", x: margin.left + 4, y: y(site.base_rate) - 6 }, `過去の平均 ${percent(site.base_rate)}`));

  const limit = priceAboveAverage(points, site.base_rate);
  const path = (subset, className) => {
    if (subset.length < 2) return;
    const d = subset.map((point, i) => `${i === 0 ? "M" : "L"}${x(point.price).toFixed(1)},${y(point.probability).toFixed(1)}`).join(" ");
    svg.append(svgElement("path", { class: className, d, fill: "none", "stroke-width": 3, "stroke-linecap": "round" }));
  };
  path(points.filter((point) => limit === null || point.price <= limit), "safe-line");
  if (limit !== null) path(points.filter((point) => point.price >= limit), "risk-line");

  try {
    const current = probability(site.model, dealInputs(deal, site.max_goodwill_ratio));
    if (deal.price >= xMin && deal.price <= xMax) {
      svg.append(svgElement("circle", { class: "current", cx: x(deal.price), cy: y(current), r: 5 }));
      const anchor = x(deal.price) > width * 0.7 ? "end" : "start";
      const shift = anchor === "end" ? -10 : 10;
      svg.append(svgElement("text", { class: "current-label", x: x(deal.price) + shift, y: y(current) + 18, "text-anchor": anchor }, `いまの買収額 ${oku(deal.price)}`));
    }
  } catch {
    // 入力が使えないときは点を描かない
  }

  summary.textContent =
    limit === null
      ? "この範囲の買収額では、見積もりは過去の平均を上回りません。"
      : `買収額が${oku(limit)}を超えると、見積もりが過去の平均を上回ります。`;
}

function render(site) {
  const deal = readDeal();
  byId("equity-ratio-value").textContent = String(Math.round(deal.equityRatio * 100));
  renderResult(site, deal);
  renderCurve(site, deal);
}

function loadPreset(site, name) {
  const example = site.examples.find((item) => item.name === name);
  if (!example) return;
  const values = example.values;
  byId("price").value = values.price;
  byId("target-net-assets").value = values.target_net_assets;
  byId("acquirer-assets").value = values.acquirer_assets;
  byId("equity-ratio").value = Math.round(values.equity_ratio * 100);
  document.querySelector(`input[name="relatedness"][value="${values.relatedness}"]`).checked = true;
  render(site);
}

async function main() {
  const response = await fetch("model.json");
  const site = await response.json();
  fillSiteNumbers(site);
  for (const input of document.querySelectorAll("input")) {
    input.addEventListener("input", () => render(site));
  }
  for (const button of document.querySelectorAll("[data-preset]")) {
    button.addEventListener("click", () => loadPreset(site, button.dataset.preset));
  }
  loadPreset(site, "C社");
}

main();
