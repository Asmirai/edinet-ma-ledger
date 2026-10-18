import io
import zipfile
from datetime import date

import pytest

from ledger.roa import company_years, delta_roa, label_acquisitions, read_summary

HEADER = [
    "要素ID",
    "項目名",
    "コンテキストID",
    "相対年度",
    "連結・個別",
    "期間・時点",
    "ユニットID",
    "単位",
    "値",
]
JGAAP_PROFIT = "jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults"
IFRS_PROFIT = "jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults"
IFRS_PLAIN_PROFIT = "jpcrp_cor:ProfitLossIFRSSummaryOfBusinessResults"
IFRS_ASSETS = "jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults"
ASSETS = "jpcrp_cor:TotalAssetsSummaryOfBusinessResults"
SINGLE_PROFIT = "jpcrp_cor:NetIncomeLossSummaryOfBusinessResults"
SINGLE = "_NonConsolidatedMember"


def _archive(rows: list[tuple[str, str, str]]) -> bytes:
    lines = ["\t".join(f'"{cell}"' for cell in HEADER)]
    for element, context, value in rows:
        cells = [element, "項目", context, "当期", "連結", "期間", "JPY", "円", value]
        lines.append("\t".join(f'"{cell}"' for cell in cells))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XBRL_TO_CSV/jpaud-aai.csv", "\r\n".join(lines[:1]).encode("utf-16"))
        archive.writestr("XBRL_TO_CSV/jpcrp030000-asr.csv", "\r\n".join(lines).encode("utf-16"))
    return buffer.getvalue()


def 連結の5期分の純利益と総資産を基準と一緒に読む() -> None:
    rows = [
        (JGAAP_PROFIT, "CurrentYearDuration", "100"),
        (ASSETS, "CurrentYearInstant", "1000"),
        (JGAAP_PROFIT, "Prior4YearDuration", "40"),
        (ASSETS, "Prior4YearInstant", "800"),
        (SINGLE_PROFIT, "CurrentYearDuration" + SINGLE, "5"),
        (ASSETS, "CurrentYearInstant" + SINGLE, "50"),
    ]

    assert read_summary(_archive(rows)) == {0: (100, 1000, "日本基準"), 4: (40, 800, "日本基準")}


def 同じ期に日本基準とIFRSがあればIFRSを読む() -> None:
    rows = [
        (JGAAP_PROFIT, "Prior1YearDuration", "20"),
        (ASSETS, "Prior1YearInstant", "800"),
        (IFRS_PROFIT, "Prior1YearDuration", "-30"),
        (IFRS_ASSETS, "Prior1YearInstant", "900"),
    ]

    assert read_summary(_archive(rows)) == {1: (-30, 900, "IFRS")}


def 親会社株主に帰属する利益が無ければ当期利益を読む() -> None:
    rows = [
        (IFRS_PLAIN_PROFIT, "CurrentYearDuration", "7"),
        (IFRS_ASSETS, "CurrentYearInstant", "70"),
    ]

    assert read_summary(_archive(rows)) == {0: (7, 70, "IFRS")}


def 連結が無ければ単体を読む() -> None:
    rows = [
        (SINGLE_PROFIT, "CurrentYearDuration" + SINGLE, "5"),
        (ASSETS, "CurrentYearInstant" + SINGLE, "50"),
    ]

    assert read_summary(_archive(rows)) == {0: (5, 50, "単体")}


def 純利益か総資産の欠けた期は読まない() -> None:
    rows = [
        (JGAAP_PROFIT, "CurrentYearDuration", "100"),
        (ASSETS, "CurrentYearInstant", "1000"),
        (JGAAP_PROFIT, "Prior1YearDuration", "－"),
        (ASSETS, "Prior1YearInstant", "900"),
        (ASSETS, "Prior2YearInstant", "800"),
    ]

    assert read_summary(_archive(rows)) == {0: (100, 1000, "日本基準")}


def 有報の決算日から各期の決算日を出し後の有報の値を使う() -> None:
    older = (date(2024, 3, 31), {0: (10, 100, "日本基準"), 1: (9, 90, "日本基準")})
    newer = (date(2025, 3, 31), {0: (12, 120, "日本基準"), 1: (11, 110, "日本基準")})

    assert company_years([newer, older]) == {
        date(2023, 3, 31): (9, 90, "日本基準"),
        date(2024, 3, 31): (11, 110, "日本基準"),
        date(2025, 3, 31): (12, 120, "日本基準"),
    }


def うるう年の2月末決算も前の期の決算日を出せる() -> None:
    summary = {1: (1, 10, "日本基準")}

    assert company_years([(date(2024, 2, 29), summary)]) == {date(2023, 2, 28): (1, 10, "日本基準")}


def 決算期を変えた会社は各有報の当期の値だけを使う() -> None:
    march = (date(2019, 3, 31), {0: (10, 100, "日本基準"), 1: (9, 90, "日本基準")})
    december = (date(2019, 12, 31), {0: (7, 110, "日本基準"), 1: (10, 100, "日本基準")})

    assert company_years([march, december]) == {
        date(2019, 3, 31): (10, 100, "日本基準"),
        date(2019, 12, 31): (7, 110, "日本基準"),
    }


def _years(
    roas: dict[int, float], assets: int = 1000, basis: str = "日本基準"
) -> dict[date, tuple[int, int, str]]:
    return {date(year, 3, 31): (round(roa * assets), assets, basis) for year, roa in roas.items()}


def 買収後3期の平均から買収前の期を引く() -> None:
    years = _years({2019: 0.02, 2021: 0.03, 2022: 0.05, 2023: 0.04})

    assert delta_roa(years, 2020) == pytest.approx(0.02)


def 前後の期が欠ければ差を出さない() -> None:
    assert delta_roa(_years({2019: 0.02, 2021: 0.03, 2022: 0.05, 2024: 0.01}), 2020) is None
    assert delta_roa(_years({2021: 0.03, 2022: 0.05, 2023: 0.04}), 2020) is None


def _row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "deal_id": 1,
        "edinetCode": "E1",
        "acquisition_date": "2020-06-01",
        "consideration_yen": 100,
    }
    return row | changes


def 同業の差の中央値を引いて成否を付ける() -> None:
    years_by_company = {
        "E1": _years({2020: 0.02, 2022: 0.05, 2023: 0.05, 2024: 0.05}),
        "E2": _years({2020: 0.02, 2022: 0.03, 2023: 0.03, 2024: 0.03}),
        "E3": _years({2020: 0.02, 2022: 0.01, 2023: 0.01, 2024: 0.01}),
    }
    industries = {"E1": "情報・通信業", "E2": "情報・通信業", "E3": "情報・通信業"}

    [row] = label_acquisitions([_row()], years_by_company, industries)

    assert row["base_year"] == 2021
    assert row["delta_roa"] == pytest.approx(0.03)
    assert row["excess_delta_roa"] == pytest.approx(0.02)
    assert row["success"] is True
    assert row["size_ratio"] == pytest.approx(0.1)
    assert row["unlabeled_reason"] is None


def _reason(years: dict[date, tuple[int, int, str]], acquired: str = "2020-06-01") -> object:
    rows = [_row(acquisition_date=acquired)]
    return label_acquisitions(rows, {"E1": years}, {"E1": "情報・通信業"})[0]["unlabeled_reason"]


def 成否を付けられない行には理由を付ける() -> None:
    assert _reason({}) == "財務の値が無い"
    assert _reason(_years({2020: 0.02}), acquired="") == "企業結合日が無い"
    assert _reason(_years({2020: 0.02, 2022: 0.05, 2023: 0.05})) == "買収後3期の有報がまだ無い"
    assert _reason(_years({2020: 0.02, 2022: 0.05, 2024: 0.05})) == "前後の ROA が欠けている"
    # 2024年に3月決算から12月決算へ変えた
    changed = _years({2020: 0.02, 2021: 0.02, 2022: 0.05, 2023: 0.05}) | {
        date(2024, 12, 31): (5, 100, "日本基準")
    }
    assert _reason(changed) == "決算期の変更"
    mixed = _years({2020: 0.02}, basis="単体") | _years({2022: 0.05, 2023: 0.05, 2024: 0.05})
    assert _reason(mixed) == "会計基準か連結・単体が変わった"


def 決算日が分かっていれば取得日の後の最初の決算日の年を基準にする() -> None:
    years = {
        date(2023, 2, 28): (1, 100, "日本基準"),
        date(2024, 2, 29): (1, 100, "日本基準"),
        date(2025, 2, 28): (1, 100, "日本基準"),
    }

    [row] = label_acquisitions([_row(acquisition_date="2024-02-29")], {"E1": years}, {})

    assert row["base_year"] == 2024


def 親会社と子会社が同じ案件を載せたら総資産の小さいほうを主にする() -> None:
    years_by_company = {
        "E1": _years({2020: 0.02, 2022: 0.05, 2023: 0.05, 2024: 0.05}, assets=1000),
        "E9": _years({2020: 0.02, 2022: 0.03, 2023: 0.03, 2024: 0.03}, assets=50000),
    }
    rows = [_row(), _row(edinetCode="E9")]

    labeled = label_acquisitions(rows, years_by_company, {"E1": "卸売業", "E9": "卸売業"})

    assert [row["primary"] for row in labeled] == [True, False]
