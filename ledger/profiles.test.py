import io
import zipfile

import pytest

from ledger.profiles import acquirer_profile

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
SINGLE = "_NonConsolidatedMember"


def _archive(rows: list[tuple[str, str, str]]) -> bytes:
    lines = ["\t".join(f'"{cell}"' for cell in HEADER)]
    for element, context, value in rows:
        cells = [element, "項目", context, "当期", "連結", "期間", "JPY", "円", value]
        lines.append("\t".join(f'"{cell}"' for cell in cells))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XBRL_TO_CSV/jpcrp030000-asr.csv", "\r\n".join(lines).encode("utf-16"))
    return buffer.getvalue()


def _element(name: str) -> str:
    return f"jpcrp_cor:{name}SummaryOfBusinessResults"


def 連結の売上の伸びと自己資本比率と現金と営業キャッシュフローの割合を出す() -> None:
    rows = [
        (_element("NetSales"), "CurrentYearDuration", "1200"),
        (_element("NetSales"), "Prior1YearDuration", "1000"),
        (_element("TotalAssets"), "CurrentYearInstant", "2000"),
        (_element("EquityToAssetRatio"), "CurrentYearInstant", "0.45"),
        (_element("CashAndCashEquivalents"), "CurrentYearInstant", "300"),
        (_element("NetCashProvidedByUsedInOperatingActivities"), "CurrentYearDuration", "-100"),
        (_element("NetSales"), "CurrentYearDuration" + SINGLE, "50"),
    ]

    profile = acquirer_profile(_archive(rows))

    assert profile["sales_growth"] == pytest.approx(0.2)
    assert profile["equity_ratio"] == pytest.approx(0.45)
    assert profile["cash_ratio"] == pytest.approx(0.15)
    assert profile["ocf_ratio"] == pytest.approx(-0.05)


def IFRSの要素と単体の値も読む() -> None:
    ifrs = [
        ("jpcrp_cor:RevenueIFRSSummaryOfBusinessResults", "CurrentYearDuration", "110"),
        ("jpcrp_cor:RevenueIFRSSummaryOfBusinessResults", "Prior1YearDuration", "100"),
        ("jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults", "CurrentYearInstant", "500"),
        (
            "jpcrp_cor:RatioOfOwnersEquityToGrossAssetsIFRSSummaryOfBusinessResults",
            "CurrentYearInstant",
            "0.3",
        ),
    ]
    single = [
        (_element("NetSales"), "CurrentYearDuration" + SINGLE, "90"),
        (_element("NetSales"), "Prior1YearDuration" + SINGLE, "100"),
        (_element("TotalAssets"), "CurrentYearInstant" + SINGLE, "400"),
    ]

    assert acquirer_profile(_archive(ifrs))["sales_growth"] == pytest.approx(0.1)
    assert acquirer_profile(_archive(ifrs))["equity_ratio"] == pytest.approx(0.3)
    assert acquirer_profile(_archive(single))["sales_growth"] == pytest.approx(-0.1)


def 書かれていない値は空にする() -> None:
    profile = acquirer_profile(_archive([(_element("NetSales"), "CurrentYearDuration", "1")]))

    assert profile == {
        "sales_growth": None,
        "equity_ratio": None,
        "cash_ratio": None,
        "ocf_ratio": None,
    }
