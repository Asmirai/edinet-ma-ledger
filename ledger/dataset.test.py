from typing import Any

from ledger.dataset import BIG_DEAL, deal_table, risk_count


def _row(deal_id: str, **changes: str) -> dict[str, str]:
    row = {
        "deal_id": deal_id,
        "edinetCode": "E1",
        "industry": "卸売業",
        "acquisition_date": "2020-06-01",
        "base_year": "2021",
        "primary": "True",
        "success": "True",
        "delta_roa": "0.01",
        "excess_delta_roa": "0.01",
        "matched_excess_delta_roa": "0.02",
        "delta_roa_1": "0.01",
        "delta_roa_2": "",
        "delta_roa_3": "0.03",
        "excess_roa_before": "0.0",
        "size_ratio": "0.2",
        "consideration_yen": "1000",
        "goodwill_yen": "300",
        "legal_form": "現金を対価とする株式取得",
        "voting_ratio": "100％",
    }
    return row | changes


def 案件ごとに列と2つの物差しをまとめる() -> None:
    rows = [
        _row("1"),
        _row("2", size_ratio="0.01", matched_excess_delta_roa="-0.01"),
        _row("3", base_year="2024"),
        _row("4", primary="False"),
    ]
    purposes = {"1-E1": ("同じ事業の拡大", "同じ事業")}
    profiles: dict[str, dict[str, float | None]] = {
        "1-E1": {"sales_growth": 0.1, "equity_ratio": 0.5, "cash_ratio": 0.2, "ocf_ratio": 0.05}
    }

    deals = deal_table(
        rows,
        {"1-E1": "卸売業"},
        purposes,
        {"1-E1": True, "2-E1": False},
        profiles,
        {"1-E1": "IFRS"},
    )

    assert [d["key"] for d in deals] == ["1-E1", "2-E1"]
    first, second = deals
    assert first["impaired"] is True
    assert first["roa_success"] is True
    assert first["purpose"] == "同じ事業の拡大"
    assert first["consideration"] == "現金"
    assert first["equity_ratio"] == 0.5
    assert first["basis"] == "IFRS"
    assert first["risk_count"] == 1
    assert second["roa_success"] is None
    assert second["purpose"] is None
    assert second["equity_ratio"] is None


def 大きい買収の境目は総資産の10パーセント() -> None:
    assert BIG_DEAL == 0.1


def 危険信号の数を数える() -> None:
    deal: dict[str, Any] = {
        "goodwill_ratio": 0.9,
        "relatedness": "遠い事業",
        "equity_ratio": 0.3,
        "size_ratio": 0.06,
    }

    assert risk_count(deal) == 3
    assert risk_count(deal | {"goodwill_ratio": 0.5, "relatedness": "同じ事業"}) == 1
    assert risk_count(deal | {"equity_ratio": None}) is None
