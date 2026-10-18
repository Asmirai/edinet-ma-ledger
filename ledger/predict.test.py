import random
from typing import Any

import pytest

from ledger.predict import (
    CATEGORICAL,
    NUMERIC,
    evaluate,
    fit_models,
    legal_form_kind,
    model_rows,
    split_by_year,
    voting_number,
)


def 法的形式を5つの種類にまとめる() -> None:
    assert legal_form_kind("現金を対価とする株式取得") == "株式取得"
    assert legal_form_kind("現金を対価とする持分の取得") == "株式取得"
    assert legal_form_kind("現金を対価とする事業譲受") == "事業譲受"
    assert legal_form_kind("吸収分割") == "事業譲受"
    assert legal_form_kind("当社を存続会社とする吸収合併") == "合併"
    assert legal_form_kind("株式交換") == "株式交換"
    assert legal_form_kind("第三者割当増資の引受け") == "その他"
    assert legal_form_kind("") == "不明"


def 議決権比率の数字を読む() -> None:
    assert voting_number("100％") == 100
    assert voting_number("５１.０％") == 51
    assert voting_number("取得前 20％ 取得後 60％") == 60
    assert voting_number("100％（うち間接保有 24.49%）") == 100
    assert voting_number("不二サッシ株式会社 97.0％、日海不二サッシ株式会社 3.0％") == 97
    assert voting_number("") is None


def _ledger_row(deal_id: int, date: str, **changes: str) -> dict[str, str]:
    row = {
        "deal_id": str(deal_id),
        "edinetCode": "E1",
        "industry": "卸売業",
        "acquisition_date": date,
        "base_year": "2019",
        "primary": "True",
        "success": "True",
        "delta_roa": "0.0",
        "excess_delta_roa": "0.0",
        "matched_excess_delta_roa": "0.01",
        "excess_roa_before": "-0.02",
        "size_ratio": "0.1",
        "consideration_yen": "1000",
        "goodwill_yen": "300",
        "legal_form": "株式取得",
        "voting_ratio": "100％",
    }
    return row | changes


def 成否を付けた主の行から列を作り直前2年の買収の件数を数える() -> None:
    rows = [
        _ledger_row(1, "2017-04-01", success="", matched_excess_delta_roa=""),
        _ledger_row(2, "2018-04-01", primary="False"),
        _ledger_row(3, "2019-04-01"),
        _ledger_row(4, "2019-05-01", matched_excess_delta_roa="-0.01"),
        # 台帳の始まりから2年たっていないので、直前2年の件数はそろわない
        _ledger_row(5, "2018-06-01"),
    ]

    built = model_rows(rows, {"3-E1": "卸売業", "4-E1": "小売業"})

    assert [r["prior_deals"] for r in built] == [3, 3, None]
    assert [r["target"] for r in built] == [True, False, True]
    assert built[0]["same_industry"] == 1.0
    assert built[1]["same_industry"] == 0.0
    assert built[0]["goodwill_ratio"] == pytest.approx(0.3)
    assert built[0]["legal_form"] == "株式取得"
    assert built[0]["voting_ratio"] == 100


def 基準の年で学習と確かめに分ける() -> None:
    rows: list[dict[str, Any]] = [{"base_year": year} for year in (2017, 2019, 2020, 2022)]

    train, test = split_by_year(rows, 2019)

    assert [r["base_year"] for r in train] == [2017, 2019]
    assert [r["base_year"] for r in test] == [2020, 2022]


def 当て方の物差しを出す() -> None:
    truth = [True, False, True, False, True, False, False, False, False, False]
    perfect = [0.9, 0.1, 0.8, 0.2, 0.7, 0.1, 0.1, 0.1, 0.1, 0.1]

    scores = evaluate(truth, perfect)

    assert scores["auc"] == pytest.approx(1.0)
    assert scores["pr_auc"] == pytest.approx(1.0)
    assert scores["base_rate"] == pytest.approx(0.3)
    assert scores["top10_rate"] == pytest.approx(1.0)
    assert evaluate(truth, [0.5] * 10)["auc"] == pytest.approx(0.5)


def どれも同じ確率なら上位10パーセントの成功率は全体の成功率にする() -> None:
    truth = [True] + [False] * 9

    assert evaluate(truth, [0.4] * 10)["top10_rate"] == pytest.approx(0.1)


def 三つのモデルを学習して確かめの物差しを並べる() -> None:
    rng = random.Random(0)
    rows: list[dict[str, Any]] = []
    for i in range(600):
        size = rng.random()
        rows.append(
            {
                "base_year": 2017 + i % 6,
                "same_industry": float(i % 2),
                "goodwill_ratio": rng.random() if i % 7 else None,
                "log_size_ratio": size,
                "excess_roa_before": rng.gauss(0, 0.02),
                "voting_ratio": 100.0,
                "prior_deals": i % 4,
                "industry": "卸売業",
                "acquiree_industry": "卸売業" if i % 3 else None,
                "legal_form": "株式取得",
                "target": rng.random() < (0.8 if size > 0.5 else 0.2),
            }
        )

    results = fit_models(rows, 2019, NUMERIC, CATEGORICAL)

    assert list(results) == ["全件の成功率", "ロジスティック回帰", "勾配ブースティング"]
    assert results["全件の成功率"]["auc"] == pytest.approx(0.5)
    assert results["ロジスティック回帰"]["auc"] > 0.7
    assert results["勾配ブースティング"]["auc"] > 0.7
