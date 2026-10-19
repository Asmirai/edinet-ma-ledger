import math
import random
from typing import Any

import pytest

from ledger.scorer import (
    build_report,
    complete_rows,
    deal_inputs,
    fit_scorer,
    holdout,
    predict,
    price_curve,
    rank_candidates,
    read_candidates,
    read_ledger,
)


def 候補の数字を4つの列にする() -> None:
    inputs = deal_inputs(
        price=50,
        target_net_assets=5,
        acquirer_assets=500,
        equity_ratio=0.7,
        relatedness="異業種",
    )

    assert inputs["goodwill_ratio"] == pytest.approx(0.9)
    assert inputs["log_size_ratio"] == pytest.approx(math.log(0.1))
    assert inputs["equity_ratio"] == 0.7
    assert inputs["relatedness"] == "異業種"


def 相手の純資産が買収額より大きければ上乗せ率は0にする() -> None:
    inputs = deal_inputs(
        price=10,
        target_net_assets=12,
        acquirer_assets=500,
        equity_ratio=0.4,
        relatedness="同業種",
    )

    assert inputs["goodwill_ratio"] == 0


def 相手の純資産がマイナスなら上乗せ率は1を超える() -> None:
    inputs = deal_inputs(
        price=10,
        target_net_assets=-2,
        acquirer_assets=500,
        equity_ratio=0.4,
        relatedness="同業種",
    )

    assert inputs["goodwill_ratio"] == pytest.approx(1.2)


def 上乗せ率が台帳で使った上限を超えたらValueErrorにする() -> None:
    with pytest.raises(ValueError):
        deal_inputs(
            price=10,
            target_net_assets=-6,
            acquirer_assets=500,
            equity_ratio=0.4,
            relatedness="同業種",
        )


def 買収額が0ならValueErrorにする() -> None:
    with pytest.raises(ValueError, match="買収額"):
        deal_inputs(
            price=0,
            target_net_assets=5,
            acquirer_assets=500,
            equity_ratio=0.4,
            relatedness="同業種",
        )


def 自社の総資産が0ならValueErrorにする() -> None:
    with pytest.raises(ValueError, match="総資産"):
        deal_inputs(
            price=10, target_net_assets=5, acquirer_assets=0, equity_ratio=0.4, relatedness="同業種"
        )


def 自己資本比率が1を超えたらValueErrorにする() -> None:
    with pytest.raises(ValueError, match="自己資本比率"):
        deal_inputs(
            price=10,
            target_net_assets=5,
            acquirer_assets=500,
            equity_ratio=1.2,
            relatedness="同業種",
        )


def 業種の関係が一覧に無ければValueErrorにする() -> None:
    with pytest.raises(ValueError, match="業種の関係"):
        deal_inputs(
            price=10,
            target_net_assets=5,
            acquirer_assets=500,
            equity_ratio=0.4,
            relatedness="わからない",
        )


def _deal(year: int, impaired: bool | None, **changes: Any) -> dict[str, Any]:
    deal: dict[str, Any] = {
        "base_year": year,
        "impaired": impaired,
        "goodwill_ratio": 0.5,
        "log_size_ratio": math.log(0.02),
        "equity_ratio": 0.5,
        "relatedness": "関連業種",
    }
    return deal | changes


def 成否と4つの列がそろう案件だけを使う() -> None:
    deals = [
        _deal(2018, True),
        _deal(2018, None),
        _deal(2018, False, goodwill_ratio=None),
        _deal(2018, False, relatedness=None),
    ]

    assert complete_rows(deals) == [deals[0]]


def _synthetic(count: int) -> list[dict[str, Any]]:
    """上乗せ率が高く、異業種ほど減損しやすい案件を作る。"""
    rng = random.Random(0)
    deals: list[dict[str, Any]] = []
    for i in range(count):
        ratio = rng.random()
        relatedness = ("同業種", "関連業種", "異業種")[i % 3]
        risk = 0.05 + 0.4 * ratio + (0.2 if relatedness == "異業種" else 0)
        deals.append(
            _deal(
                2016 + i % 7,
                rng.random() < risk,
                goodwill_ratio=ratio,
                log_size_ratio=math.log(rng.uniform(0.001, 0.3)),
                equity_ratio=rng.random(),
                relatedness=relatedness,
            )
        )
    return deals


def 危ない案件ほど高い確率を出す() -> None:
    model = fit_scorer(_synthetic(900))
    safe = deal_inputs(
        price=10, target_net_assets=9, acquirer_assets=500, equity_ratio=0.5, relatedness="同業種"
    )
    risky = deal_inputs(
        price=10,
        target_net_assets=0.5,
        acquirer_assets=500,
        equity_ratio=0.5,
        relatedness="異業種",
    )

    low, high = predict(model, [safe, risky])

    assert 0 < low < high < 1


def 学習の年の後の案件を危ない順に5つに分けて失敗率を出す() -> None:
    result = holdout(_synthetic(900), last_train_year=2019)

    groups = result["groups"]
    assert result["n"] == sum(g["n"] for g in groups)
    assert len(groups) == 5
    assert groups[0]["rate"] < groups[-1]["rate"]
    assert groups[0]["mean_probability"] < groups[-1]["mean_probability"]
    assert result["auc"] > 0.6


def 学習の年までを学習に使い次の年からを確かめに使う() -> None:
    deals = _synthetic(900)

    result = holdout(deals, last_train_year=2019)

    assert result["train_n"] == sum(1 for d in deals if d["base_year"] <= 2019)
    assert result["n"] == sum(1 for d in deals if d["base_year"] >= 2020)


def 買収額を上げるほど確率が上がる() -> None:
    model = fit_scorer(_synthetic(900))

    curve = price_curve(
        model,
        target_net_assets=5,
        acquirer_assets=500,
        equity_ratio=0.5,
        relatedness="関連業種",
        prices=[6, 10, 20, 40],
    )

    assert curve == sorted(curve)
    assert curve[0] < curve[-1]


def 自社と候補のCSVを読む(tmp_path: Any) -> None:
    path = tmp_path / "candidates.csv"
    path.write_text(
        "name,price,target_net_assets,acquirer_assets,equity_ratio,relatedness\n"
        "B社,50,5,500,0.7,異業種\n"
        "C社,10,8,500,0.7,関連業種\n",
        encoding="utf-8",
    )

    candidates = read_candidates(path)

    assert [c["name"] for c in candidates] == ["B社", "C社"]
    assert candidates[0]["inputs"]["goodwill_ratio"] == pytest.approx(0.9)
    assert candidates[1]["inputs"]["relatedness"] == "関連業種"


def 公開CSVから成否と4つの列がそろう行を読む(tmp_path: Any) -> None:
    header = (
        "deal_id,primary,consideration_yen,goodwill_yen,size_ratio,relatedness,"
        "acquirer_equity_ratio,base_year,goodwill_impaired\n"
    )
    path = tmp_path / "ma_ledger.csv"
    path.write_text(
        header
        + "1,True,1000,800,0.05,異業種,0.7,2019,True\n"
        # 主の行でない、減損を判定していない、のれんの割合が1.5を超える、基準の年が2022年より後
        + "1,False,1000,800,0.05,異業種,0.7,2019,\n"
        + "2,True,1000,800,0.05,,0.7,2019,\n"
        + "3,True,1000,2000,0.05,同業種,0.4,2020,False\n"
        + "4,True,1000,100,0.05,同業種,0.4,2023,False\n",
        encoding="utf-8-sig",
    )

    rows = read_ledger(path)

    assert len(rows) == 1
    assert rows[0]["base_year"] == 2019
    assert rows[0]["impaired"] is True
    assert rows[0]["goodwill_ratio"] == pytest.approx(0.8)
    assert rows[0]["log_size_ratio"] == pytest.approx(math.log(0.05))
    assert rows[0]["equity_ratio"] == pytest.approx(0.7)
    assert rows[0]["relatedness"] == "異業種"


def 候補を失敗の確率の低い順に並べる() -> None:
    model = fit_scorer(_synthetic(900))
    candidates: list[dict[str, Any]] = [
        {
            "name": "危ない",
            "inputs": deal_inputs(
                price=10,
                target_net_assets=0.5,
                acquirer_assets=500,
                equity_ratio=0.5,
                relatedness="異業種",
            ),
        },
        {
            "name": "安全",
            "inputs": deal_inputs(
                price=10,
                target_net_assets=9,
                acquirer_assets=500,
                equity_ratio=0.5,
                relatedness="同業種",
            ),
        },
    ]

    ranked = rank_candidates(model, candidates)

    assert [name for name, _ in ranked] == ["安全", "危ない"]
    assert ranked[0][1] < ranked[1][1]


def 確かめの結果を群ごとの表にする() -> None:
    result: dict[str, Any] = {
        "train_n": 850,
        "n": 869,
        "base_rate": 0.173,
        "auc": 0.6874,
        "groups": [{"n": 173, "rate": 0.029, "mean_probability": 0.075}],
    }

    report = build_report(result)

    assert "2019年度までの 850 件で学習し、2020年度以降の 869 件で確かめた" in report
    assert "AUC 0.687" in report
    assert "| 1 | 173 | 2.9% | 7.5% |" in report
