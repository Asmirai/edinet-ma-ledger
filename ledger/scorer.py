"""自社と買収の候補の数字から、その買収でのれんを減損する（失敗する）確率を出す。

使う数字は、買収の前に誰でも集められる4つだけにする。
- 上乗せ率：（買収額 − 取得する持分に当たる相手の純資産）÷ 買収額。台帳の「取得原価のうちのれんの割合」に当たる
- 規模：買収額 ÷ 自社の総資産（対数にして使う）
- 自社の自己資本比率
- 業種の関係（同業種・関連業種・異業種）
目的・対価・現金比率・買収前の ROA を足しても、2020年度以降で確かめた当たり具合はほとんど変わらなかった。

確かめ方の限界：4つの数字は全期間のデータを見て選んだ（2019年度までだけでも同じ向きの差が出る）。
また、学習に使う2019年度までの買収の成否は、3年後の2022年度までに出そろう。

使い方：
- uv run python -m ledger.scorer：2019年度までで学習し、2020年度以降で確かめた結果を data/scorer.md に書く
- uv run python -m ledger.scorer candidates.csv：全件で学習したモデルで、候補ごとの確率を出す
  （列は name, price, target_net_assets, acquirer_assets, equity_ratio, relatedness。金額の単位はそろえる。
  target_net_assets は、取得する持分に当たる相手の純資産。一部だけ買うなら、その割合を掛けた額）
"""

import csv
import importlib
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from ledger.dataset import load_deals
from ledger.features import MAX_GOODWILL_RATIO

REPORT_PATH = Path("data/scorer.md")
LAST_TRAIN_YEAR = 2019
NUMERIC = ("goodwill_ratio", "log_size_ratio", "equity_ratio")
CATEGORICAL = ("relatedness",)
# 候補の CSV に書く業種の関係と、台帳の分類（relatedness）の対応
RELATEDNESS = {"同業種": "同じ事業", "関連業種": "近い事業", "異業種": "遠い事業"}
GROUPS = 5
# scikit-learn には型の情報が無いので、境目で Any として扱う
sk_compose: Any = importlib.import_module("sklearn.compose")
sk_linear: Any = importlib.import_module("sklearn.linear_model")
sk_metrics: Any = importlib.import_module("sklearn.metrics")
sk_pipeline: Any = importlib.import_module("sklearn.pipeline")
sk_preprocessing: Any = importlib.import_module("sklearn.preprocessing")


def deal_inputs(
    price: float,
    target_net_assets: float,
    acquirer_assets: float,
    equity_ratio: float,
    relatedness: str,
) -> dict[str, Any]:
    """候補1件の数字を、モデルに入れる4つの列にする。

    target_net_assets は、取得する持分に当たる相手の純資産。
    相手の純資産が買収額より大きいとのれんは生じないので、上乗せ率は0にする（学習した案件には無い範囲）。
    上乗せ率が台帳で使った上限を超える候補は、学習した範囲から外れるので受け付けない。
    """
    if price <= 0 or acquirer_assets <= 0:
        raise ValueError("買収額と自社の総資産は0より大きくする")
    if not 0 <= equity_ratio <= 1:
        raise ValueError("自己資本比率は0から1の間にする")
    if relatedness not in RELATEDNESS:
        raise ValueError(f"業種の関係は {'・'.join(RELATEDNESS)} のどれかにする")
    goodwill_ratio = max(0.0, (price - target_net_assets) / price)
    if goodwill_ratio > MAX_GOODWILL_RATIO:
        raise ValueError(f"上乗せ率が {MAX_GOODWILL_RATIO} を超える候補は、学習した範囲の外になる")
    return {
        "goodwill_ratio": goodwill_ratio,
        "log_size_ratio": math.log(price / acquirer_assets),
        "equity_ratio": equity_ratio,
        "relatedness": RELATEDNESS[relatedness],
    }


def complete_rows(deals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """減損したかと4つの列がそろう案件。"""
    return [
        d
        for d in deals
        if d["impaired"] is not None and all(d[name] is not None for name in NUMERIC + CATEGORICAL)
    ]


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame({name: [r[name] for r in rows] for name in NUMERIC + CATEGORICAL})


def fit_scorer(deals: list[dict[str, Any]]) -> Any:
    """4つの列から減損を当てるロジスティック回帰を、列のそろう案件で学習する。"""
    rows = complete_rows(deals)
    model = sk_pipeline.make_pipeline(
        sk_compose.ColumnTransformer(
            [
                ("numeric", sk_preprocessing.StandardScaler(), list(NUMERIC)),
                (
                    "categorical",
                    sk_preprocessing.OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    list(CATEGORICAL),
                ),
            ]
        ),
        sk_linear.LogisticRegression(max_iter=1000),
    )
    model.fit(_frame(rows), [bool(r["impaired"]) for r in rows])
    return model


def predict(model: Any, inputs: list[dict[str, Any]]) -> list[float]:
    return [float(p) for p in model.predict_proba(_frame(inputs))[:, 1]]


def holdout(deals: list[dict[str, Any]], last_train_year: int) -> dict[str, Any]:
    """基準の年が last_train_year までの案件で学習し、その後の案件で確かめる。

    確かめの案件をモデルの確率の低い順に5つに分け、群ごとの件数・失敗率・確率の平均を出す。
    """
    rows = complete_rows(deals)
    train = [r for r in rows if r["base_year"] <= last_train_year]
    model = fit_scorer(train)
    test = [r for r in rows if r["base_year"] > last_train_year]
    ranked = sorted(zip(predict(model, test), (bool(r["impaired"]) for r in test)))
    groups: list[dict[str, Any]] = []
    for i in range(GROUPS):
        members = ranked[len(ranked) * i // GROUPS : len(ranked) * (i + 1) // GROUPS]
        groups.append(
            {
                "n": len(members),
                "rate": sum(t for _, t in members) / len(members),
                "mean_probability": sum(p for p, _ in members) / len(members),
            }
        )
    return {
        "train_n": len(train),
        "n": len(test),
        "base_rate": sum(t for _, t in ranked) / len(ranked),
        "auc": float(sk_metrics.roc_auc_score([t for _, t in ranked], [p for p, _ in ranked])),
        "groups": groups,
    }


def price_curve(
    model: Any,
    target_net_assets: float,
    acquirer_assets: float,
    equity_ratio: float,
    relatedness: str,
    prices: list[float],
) -> list[float]:
    """同じ候補を、買収額だけ変えて買ったときの確率。"""
    return predict(
        model,
        [
            deal_inputs(price, target_net_assets, acquirer_assets, equity_ratio, relatedness)
            for price in prices
        ],
    )


def read_candidates(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return [
            {
                "name": row["name"],
                "inputs": deal_inputs(
                    float(row["price"]),
                    float(row["target_net_assets"]),
                    float(row["acquirer_assets"]),
                    float(row["equity_ratio"]),
                    row["relatedness"],
                ),
            }
            for row in csv.DictReader(file)
        ]


def main() -> None:
    deals = load_deals()
    if len(sys.argv) > 1:
        candidates = read_candidates(Path(sys.argv[1]))
        model = fit_scorer(deals)
        scored = zip(predict(model, [c["inputs"] for c in candidates]), candidates)
        for probability, candidate in sorted(scored, key=lambda pair: pair[0]):
            print(f"{candidate['name']}\t{probability:.1%}")
        return
    result = holdout(deals, LAST_TRAIN_YEAR)
    lines = [
        "# 4つの数字で、のれんの減損を当てる",
        "",
        f"- {LAST_TRAIN_YEAR}年度までの {result['train_n']} 件で学習し、"
        f"{LAST_TRAIN_YEAR + 1}年度以降の {result['n']} 件で確かめた",
        f"- 確かめの案件の減損率 {result['base_rate']:.1%}、AUC {result['auc']:.3f}",
        "- 4つの数字は全期間のデータを見て選んだ。2019年度までだけでも同じ向きの差が出る",
        "- 学習に使った買収の成否は、2022年度までに出そろう。確かめの期間と重なる",
        "- 確率は実際より低めに出る（学習期間のほうが減損率が低い）。並べる順は使えるが、値は目安",
        "",
        "| 確率の低い順の群 | 件数 | 減損した割合 | 確率の平均 |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {i + 1} | {g['n']} | {g['rate']:.1%} | {g['mean_probability']:.1%} |"
        for i, g in enumerate(result["groups"])
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
