// 失敗確率の計算。ledger/scorer.py の deal_inputs と、export_model が書き出す係数を使った
// ロジスティック回帰の式（scorer.test.py の _probability_from_export と同じ）で計算する。
// 画面（app.js）から切り離し、Node のテスト（model.test.mjs）で Python の結果と突き合わせる。

export const RELATEDNESS = ["同業種", "関連業種", "異業種"];

/** 上乗せ率が学習した範囲の外にあるときの誤り。曲線では、その買収額を描かずに飛ばす。 */
export class OutOfRangeError extends Error {}

/**
 * 候補の数字を、モデルに入れる4つの列にする。使えない数字なら Error を投げる。
 * 相手の純資産が買収額より大きいとのれんは生じないので、上乗せ率は0にする。
 */
export function dealInputs(
  { price, targetNetAssets, acquirerAssets, equityRatio, relatedness },
  maxGoodwillRatio,
) {
  if (!(price > 0) || !(acquirerAssets > 0)) {
    throw new Error("買収額と自社の総資産は0より大きくしてください");
  }
  if (!(equityRatio >= 0 && equityRatio <= 1)) {
    throw new Error("自己資本比率は0%から100%の間にしてください");
  }
  if (!RELATEDNESS.includes(relatedness)) {
    throw new Error("業種の関係は、同業種・関連業種・異業種のどれかにしてください");
  }
  const goodwillRatio = Math.max(0, (price - targetNetAssets) / price);
  if (goodwillRatio > maxGoodwillRatio) {
    throw new OutOfRangeError(
      `上乗せ率が${Math.round(maxGoodwillRatio * 100)}%を超えると、学習した範囲の外になります`,
    );
  }
  return {
    goodwill_ratio: goodwillRatio,
    log_size_ratio: Math.log(price / acquirerAssets),
    equity_ratio: equityRatio,
    relatedness,
  };
}

/** export_model の係数から、のれんを減損する確率を計算する。 */
export function probability(model, inputs) {
  let z = model.intercept + (model.relatedness[inputs.relatedness] ?? 0);
  for (const [name, spec] of Object.entries(model.numeric)) {
    z += (spec.coef * (inputs[name] - spec.mean)) / spec.scale;
  }
  return 1 / (1 + Math.exp(-z));
}

/**
 * 買収額だけを変えたときの確率。上乗せ率が上限を超える買収額は含めない。
 * 戻り値は { price, probability } の配列で、買収額の小さい順。ほかの数字が使えなければ Error を投げる。
 */
export function priceCurve(site, deal, prices) {
  const points = [];
  for (const price of prices) {
    try {
      const inputs = dealInputs({ ...deal, price }, site.max_goodwill_ratio);
      points.push({ price, probability: probability(site.model, inputs) });
    } catch (problem) {
      if (!(problem instanceof OutOfRangeError)) throw problem;
    }
  }
  return points;
}

/** 確率が平均を最初に上回る買収額。曲線の範囲で上回らなければ null。 */
export function priceAboveAverage(points, baseRate) {
  const found = points.find((point) => point.probability > baseRate);
  return found ? found.price : null;
}
