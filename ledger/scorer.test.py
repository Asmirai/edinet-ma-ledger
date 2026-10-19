import math
import random
from typing import Any

import pytest

from ledger.scorer import (
    complete_rows,
    deal_inputs,
    fit_scorer,
    holdout,
    predict,
    price_curve,
    read_candidates,
)


def 候補の数字を4つの列にし事業の似かたを台帳の分類に読み替える() -> None:
    inputs = deal_inputs(
        price=50,
        target_net_assets=5,
        acquirer_assets=500,
        equity_ratio=0.7,
        relatedness="似ていない事業",
    )

    assert inputs["goodwill_ratio"] == pytest.approx(0.9)
    assert inputs["log_size_ratio"] == pytest.approx(math.log(0.1))
    assert inputs["equity_ratio"] == 0.7
    assert inputs["relatedness"] == "遠い事業"


def 相手の純資産が買収額より大きければ上乗せ率は0にする() -> None:
    inputs = deal_inputs(
        price=10,
        target_net_assets=12,
        acquirer_assets=500,
        equity_ratio=0.4,
        relatedness="同じ事業",
    )

    assert inputs["goodwill_ratio"] == 0


def 相手の純資産がマイナスなら上乗せ率は1を超える() -> None:
    inputs = deal_inputs(
        price=10,
        target_net_assets=-2,
        acquirer_assets=500,
        equity_ratio=0.4,
        relatedness="同じ事業",
    )

    assert inputs["goodwill_ratio"] == pytest.approx(1.2)


def 上乗せ率が台帳で使った上限を超えたらValueErrorにする() -> None:
    with pytest.raises(ValueError):
        deal_inputs(
            price=10,
            target_net_assets=-6,
            acquirer_assets=500,
            equity_ratio=0.4,
            relatedness="同じ事業",
        )


def 使えない数字ならValueErrorにする() -> None:
    valid: dict[str, Any] = {
        "price": 10,
        "target_net_assets": 5,
        "acquirer_assets": 500,
        "equity_ratio": 0.4,
        "relatedness": "同じ事業",
    }
    invalid: list[dict[str, Any]] = [
        {"price": 0},
        {"acquirer_assets": 0},
        {"equity_ratio": 1.2},
        {"relatedness": "わからない"},
    ]
    for changes in invalid:
        with pytest.raises(ValueError):
            deal_inputs(**(valid | changes))


def _deal(year: int, impaired: bool | None, **changes: Any) -> dict[str, Any]:
    deal: dict[str, Any] = {
        "base_year": year,
        "impaired": impaired,
        "goodwill_ratio": 0.5,
        "log_size_ratio": math.log(0.02),
        "equity_ratio": 0.5,
        "relatedness": "近い事業",
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
    """上乗せ率が高く、遠い事業ほど減損しやすい案件を作る。"""
    rng = random.Random(0)
    deals: list[dict[str, Any]] = []
    for i in range(count):
        ratio = rng.random()
        relatedness = ("同じ事業", "近い事業", "遠い事業")[i % 3]
        risk = 0.05 + 0.4 * ratio + (0.2 if relatedness == "遠い事業" else 0)
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
        price=10, target_net_assets=9, acquirer_assets=500, equity_ratio=0.5, relatedness="同じ事業"
    )
    risky = deal_inputs(
        price=10,
        target_net_assets=0.5,
        acquirer_assets=500,
        equity_ratio=0.5,
        relatedness="似ていない事業",
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
        relatedness="似た事業",
        prices=[6, 10, 20, 40],
    )

    assert curve == sorted(curve)
    assert curve[0] < curve[-1]


def 自社と候補のCSVを読む(tmp_path: Any) -> None:
    path = tmp_path / "candidates.csv"
    path.write_text(
        "name,price,target_net_assets,acquirer_assets,equity_ratio,relatedness\n"
        "B社,50,5,500,0.7,似ていない事業\n"
        "C社,10,8,500,0.7,似た事業\n",
        encoding="utf-8",
    )

    candidates = read_candidates(path)

    assert [c["name"] for c in candidates] == ["B社", "C社"]
    assert candidates[0]["inputs"]["goodwill_ratio"] == pytest.approx(0.9)
    assert candidates[1]["inputs"]["relatedness"] == "近い事業"
