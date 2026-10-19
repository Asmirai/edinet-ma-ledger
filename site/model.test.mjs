// model.js が、Python（ledger/scorer.py）と同じ確率を出すことを確かめる。
// 期待する値は、uv run python -m ledger.scorer --export-model site/model.json が書いた例の候補の確率。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { dealInputs, priceAboveAverage, priceCurve, probability } from "./model.js";

const site = JSON.parse(readFileSync(new URL("./model.json", import.meta.url), "utf-8"));

test("例の候補の確率が Python の確率と一致する", () => {
  for (const example of site.examples) {
    const actual = probability(site.model, example.inputs);
    assert.ok(
      Math.abs(actual - example.probability) < 1e-12,
      `${example.name}: ${actual} と ${example.probability}`,
    );
  }
});

test("候補の数字から、Python と同じ4つの列を作る", () => {
  const inputs = dealInputs(
    { price: 50, targetNetAssets: 5, acquirerAssets: 500, equityRatio: 0.7, relatedness: "異業種" },
    1.5,
  );
  assert.ok(Math.abs(inputs.goodwill_ratio - 0.9) < 1e-12);
  assert.ok(Math.abs(inputs.log_size_ratio - Math.log(0.1)) < 1e-12);
  assert.equal(inputs.equity_ratio, 0.7);
  assert.equal(inputs.relatedness, "異業種");
});

test("相手の純資産が買収額より大きければ上乗せ率は0にする", () => {
  const inputs = dealInputs(
    { price: 10, targetNetAssets: 12, acquirerAssets: 500, equityRatio: 0.4, relatedness: "同業種" },
    1.5,
  );
  assert.equal(inputs.goodwill_ratio, 0);
});

test("上乗せ率が上限を超えたら止める", () => {
  assert.throws(
    () =>
      dealInputs(
        { price: 10, targetNetAssets: -6, acquirerAssets: 500, equityRatio: 0.4, relatedness: "同業種" },
        1.5,
      ),
    /上乗せ率/,
  );
});

test("買収額が0なら止める", () => {
  assert.throws(
    () =>
      dealInputs(
        { price: 0, targetNetAssets: 5, acquirerAssets: 500, equityRatio: 0.4, relatedness: "同業種" },
        1.5,
      ),
    /買収額/,
  );
});

test("業種の関係が一覧に無ければ止める", () => {
  assert.throws(
    () =>
      dealInputs(
        { price: 10, targetNetAssets: 5, acquirerAssets: 500, equityRatio: 0.4, relatedness: "不明" },
        1.5,
      ),
    /業種の関係/,
  );
});

test("買収額を上げるほど確率が上がり、平均を上回る買収額を返す", () => {
  const deal = { targetNetAssets: 6, acquirerAssets: 500, equityRatio: 0.55, relatedness: "関連業種" };
  const prices = Array.from({ length: 60 }, (_, i) => 6.5 + i * 0.5);
  const points = priceCurve(site, deal, prices);
  for (let i = 1; i < points.length; i += 1) {
    assert.ok(points[i].probability > points[i - 1].probability);
  }
  const limit = priceAboveAverage(points, site.base_rate);
  assert.ok(limit !== null && limit > 13 && limit < 15, `${limit}`);
});

test("上乗せ率の上限のほかに使えない数字があれば、曲線を作らずに止める", () => {
  const deal = { targetNetAssets: 6, acquirerAssets: 0, equityRatio: 0.55, relatedness: "関連業種" };
  assert.throws(() => priceCurve(site, deal, [10, 20]), /総資産/);
});
