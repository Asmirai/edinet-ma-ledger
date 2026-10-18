"""買い手の買収直前の状態（売上の伸び、自己資本比率、現金と営業キャッシュフローの厚さ）を有報から読む。

買収した期の前の期の有報1冊の「主要な経営指標等の推移」から、当期と前期の値を読む。連結を優先し、無ければ単体。
"""

import csv
import io
import re
import zipfile

from ledger.notes import REPORT_CSV_PREFIX

SINGLE_MEMBER = "_NonConsolidatedMember"
# 項目ごとに、会計基準や業種で名前が違う要素を、使う順に並べる
ELEMENTS = {
    "sales": (
        "NetSales",
        "RevenueIFRS",
        "RevenuesUSGAAP",
        "OperatingRevenue1",
        "GrossOperatingRevenue",
    ),
    "assets": ("TotalAssets", "TotalAssetsIFRS", "TotalAssetsUSGAAP"),
    "equity_ratio": (
        "EquityToAssetRatio",
        "RatioOfOwnersEquityToGrossAssetsIFRS",
        "EquityToAssetRatioIFRS",
        "EquityToAssetRatioUSGAAP",
    ),
    "cash": (
        "CashAndCashEquivalents",
        "CashAndCashEquivalentsIFRS",
        "CashAndCashEquivalentsUSGAAP",
    ),
    "ocf": (
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivitiesIFRS",
        "CashFlowsFromUsedInOperatingActivitiesUSGAAP",
    ),
}
NUMBER = re.compile(r"-?\d+(\.\d+)?")


def acquirer_profile(archive: bytes) -> dict[str, float | None]:
    """売上の伸び（当期 ÷ 前期 − 1）、自己資本比率、現金 ÷ 総資産、営業キャッシュフロー ÷ 総資産。読めなければ None。"""
    values: dict[tuple[bool, str, str], float] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        for name in sorted(files.namelist()):
            if not name.startswith(REPORT_CSV_PREFIX):
                continue
            text = files.read(name).decode("utf-16")
            for row in list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]:
                element, context, value = row[0], row[2], row[8]
                if not element.endswith("SummaryOfBusinessResults") or not NUMBER.fullmatch(value):
                    continue
                single = context.endswith(SINGLE_MEMBER)
                local = element.split(":")[-1].removesuffix("SummaryOfBusinessResults")
                period = "当期" if context.startswith("CurrentYear") else "前期"
                if context.startswith(("CurrentYear", "Prior1Year")):
                    values[(single, local, period)] = float(value)
    basis = not any(not single for single, _, _ in values)

    def pick(item: str, period: str = "当期") -> float | None:
        found = (values.get((basis, local, period)) for local in ELEMENTS[item])
        return next((v for v in found if v is not None), None)

    sales, prior_sales, assets = pick("sales"), pick("sales", "前期"), pick("assets")
    cash, ocf = pick("cash"), pick("ocf")
    has_assets = assets is not None and assets > 0
    return {
        "sales_growth": sales / prior_sales - 1 if sales is not None and prior_sales else None,
        "equity_ratio": pick("equity_ratio"),
        "cash_ratio": cash / assets if cash is not None and has_assets and assets else None,
        "ocf_ratio": ocf / assets if ocf is not None and has_assets and assets else None,
    }
