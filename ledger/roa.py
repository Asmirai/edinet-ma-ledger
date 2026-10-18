"""買収した会社の ROA が買収の前後でどう変わったかを有報から出し、同業と比べて成否を付ける。

ROA は、親会社株主に帰属する当期純利益 ÷ 期末の総資産。有報の「主要な経営指標等の推移」には5期分が載っている。
企業結合日を含む事業年度を基準の期とし、買収後3期の ROA の平均から買収前の期の ROA を引いた差（ΔROA）を出す。
同じ業種・同じ基準の年の会社の ΔROA の中央値を引いたものを超過 ΔROA とし、0 より大きければ成功とする。

使い方：uv run python -m ledger.roa
（data/documents の有報の ZIP と、data/codelist/EdinetcodeDlInfo.csv（EDINET コードリスト）を先に置いておく）
"""

import csv
import io
import json
import re
import statistics
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, cast

from ledger.acquisitions import ACQUISITIONS_PATH
from ledger.notes import DOCUMENTS_DIR, NOTES_DIR, REPORT_CSV_PREFIX

FINANCIALS_PATH = Path("data/financials.json")
EDINET_CODES_PATH = Path("data/codelist/EdinetcodeDlInfo.csv")
LABELED_PATH = Path("data/acquisitions_roa.csv")
# 日本基準・IFRS・米国基準の連結の値。どれも無い会社（子会社を持たない会社）は単体の値を使う
CONSOLIDATED_PROFITS = (
    "jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
    "jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
    "jpcrp_cor:NetIncomeLossAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
)
CONSOLIDATED_ASSETS = (
    "jpcrp_cor:TotalAssetsSummaryOfBusinessResults",
    "jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults",
    "jpcrp_cor:TotalAssetsUSGAAPSummaryOfBusinessResults",
)
SINGLE_PROFIT = "jpcrp_cor:NetIncomeLossSummaryOfBusinessResults"
SINGLE_ASSETS = "jpcrp_cor:TotalAssetsSummaryOfBusinessResults"
SINGLE_MEMBER = "_NonConsolidatedMember"
# 「CurrentYearDuration」「Prior3YearInstant」など。何期前かを読む
CONTEXT = re.compile(r"(CurrentYear|Prior(\d)Year)(Duration|Instant)")
YEARS_AFTER = 3


def read_summary(archive: bytes) -> dict[int, tuple[int, int]]:
    """有報の CSV の ZIP から、何期前か → （純利益、総資産）を読む。連結を優先し、無ければ単体。

    純利益か総資産のどちらかが欠けた期は入れない。
    """
    profits: dict[tuple[bool, int], int] = {}
    assets: dict[tuple[bool, int], int] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        for name in sorted(files.namelist()):
            if not name.startswith(REPORT_CSV_PREFIX):
                continue
            # UTF-16（BOM 付き）のタブ区切り。1行目は見出し
            text = files.read(name).decode("utf-16")
            for row in list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]:
                element, context, value = row[0], row[2], row[8]
                match = CONTEXT.fullmatch(context.removesuffix(SINGLE_MEMBER))
                if match is None or not re.fullmatch(r"-?\d+", value):
                    continue
                single = context.endswith(SINGLE_MEMBER)
                key = (single, int(match.group(2) or 0))
                if single:
                    is_profit, is_assets = element == SINGLE_PROFIT, element == SINGLE_ASSETS
                else:
                    is_profit = element in CONSOLIDATED_PROFITS
                    is_assets = element in CONSOLIDATED_ASSETS
                if is_profit:
                    profits[key] = int(value)
                if is_assets:
                    assets[key] = int(value)
    consolidated = {k for (single, k) in profits if not single and (single, k) in assets}
    single_basis = not consolidated
    return {
        years_ago: (profits[(single_basis, years_ago)], assets[(single_basis, years_ago)])
        for (single, years_ago) in sorted(profits)
        if single == single_basis and (single, years_ago) in assets
    }


def company_years(
    reports: list[tuple[date, dict[int, tuple[int, int]]]],
) -> dict[date, tuple[int, int]]:
    """有報の決算日と5期分の値から、各期の決算日 → （純利益、総資産）を作る。同じ期は後の有報の値を使う。"""
    years: dict[date, tuple[int, int]] = {}
    for period_end, summary in sorted(reports):
        for years_ago, values in summary.items():
            year = period_end.year - years_ago
            # うるう年の2月29日決算は、前の年では28日にする
            day = min(period_end.day, 28) if period_end.month == 2 else period_end.day
            years[period_end.replace(year=year, day=day)] = values
    return years


def delta_roa(years: dict[date, tuple[int, int]], base_year: int) -> float | None:
    """買収後3期の ROA の平均から、買収前の期の ROA を引く。期は決算日の年で数える。欠けた期があれば None。"""
    roas = {end.year: profit / total for end, (profit, total) in years.items() if total > 0}
    after = [roas.get(base_year + offset) for offset in range(1, YEARS_AFTER + 1)]
    before = roas.get(base_year - 1)
    if before is None or None in after:
        return None
    return statistics.mean(a for a in after if a is not None) - before


def label_acquisitions(
    rows: list[dict[str, Any]],
    years_by_company: dict[str, dict[date, tuple[int, int]]],
    industries: dict[str, str],
) -> list[dict[str, Any]]:
    """台帳の行に、基準の期、ΔROA、超過 ΔROA、成否、買収の大きさ、主の行かどうか、空にした理由を付ける。

    親会社と子会社が同じ案件を載せたときは、買収前の総資産が小さいほう（直接買った会社に近いほう）を主の行にする。
    """
    deltas: defaultdict[tuple[str, int], list[float]] = defaultdict(list)
    for code, years in years_by_company.items():
        if code not in industries:
            continue
        for base_year in {end.year + 1 for end in years}:  # 買収前の期が base_year − 1
            delta = delta_roa(years, base_year)
            if delta is not None:
                deltas[(industries[code], base_year)].append(delta)
    medians = {key: statistics.median(values) for key, values in deltas.items()}

    labeled: list[dict[str, Any]] = []
    for row in rows:
        result: dict[str, Any] = dict.fromkeys(
            ("base_year", "delta_roa", "excess_delta_roa", "success", "size_ratio", "assets_before")
        )
        result["industry"] = industries.get(row["edinetCode"])
        years = years_by_company.get(row["edinetCode"])
        acquired = row["acquisition_date"]
        if not acquired:
            result["unlabeled_reason"] = "企業結合日が無い"
        elif not years:
            result["unlabeled_reason"] = "財務の値が無い"
        else:
            acquired_on = date.fromisoformat(acquired)
            last_end = max(years)
            day = min(last_end.day, 28) if last_end.month == 2 else last_end.day
            base_end = date(acquired_on.year, last_end.month, day)
            base_year = base_end.year if base_end >= acquired_on else base_end.year + 1
            result["base_year"] = base_year
            before = next((v for end, v in years.items() if end.year == base_year - 1), None)
            if before is not None:
                result["assets_before"] = before[1]
                if row["consideration_yen"] is not None and before[1] > 0:
                    result["size_ratio"] = int(row["consideration_yen"]) / before[1]
            result["delta_roa"] = delta_roa(years, base_year)
            median = medians.get((result["industry"] or "", base_year))
            if result["delta_roa"] is None:
                result["unlabeled_reason"] = "前後の ROA がそろわない"
            elif median is None:
                result["unlabeled_reason"] = "業種が無い"
            else:
                result["excess_delta_roa"] = result["delta_roa"] - median
                result["success"] = result["excess_delta_roa"] > 0
                result["unlabeled_reason"] = None
        labeled.append(row | result)

    smallest: dict[Any, float] = {}
    for row in labeled:
        assets = row["assets_before"] if row["assets_before"] is not None else float("inf")
        smallest[row["deal_id"]] = min(smallest.get(row["deal_id"], float("inf")), assets)
    primaries: set[Any] = set()
    for row in labeled:
        assets = row["assets_before"] if row["assets_before"] is not None else float("inf")
        row["primary"] = row["deal_id"] not in primaries and assets == smallest[row["deal_id"]]
        if row["primary"]:
            primaries.add(row["deal_id"])
    return labeled


def main() -> None:
    reports_by_company: defaultdict[str, list[tuple[date, dict[int, tuple[int, int]]]]] = (
        defaultdict(list)
    )
    saved: dict[str, Any]
    if FINANCIALS_PATH.exists():
        saved = cast(dict[str, Any], json.loads(FINANCIALS_PATH.read_text(encoding="utf-8")))
    else:
        saved = {}
        for notes_path in sorted(NOTES_DIR.glob("*.json")):
            report = json.loads(notes_path.read_text(encoding="utf-8"))
            archive = (DOCUMENTS_DIR / f"{report['docID']}.zip").read_bytes()
            saved[report["docID"]] = {
                "edinetCode": report["edinetCode"],
                "periodEnd": report["periodEnd"],
                "summary": read_summary(archive),
            }
        # 3万冊の ZIP を読むのに時間がかかるので、読んだ値を残しておく
        FINANCIALS_PATH.write_text(json.dumps(saved), encoding="utf-8")
    for report in cast(list[dict[str, Any]], list(saved.values())):
        values = cast(dict[str, list[int]], report["summary"])
        summary = {int(k): (v[0], v[1]) for k, v in values.items()}
        reports_by_company[str(report["edinetCode"])].append(
            (date.fromisoformat(str(report["periodEnd"])), summary)
        )
    years_by_company = {
        code: company_years(reports) for code, reports in reports_by_company.items()
    }
    # EDINET コードリスト。1行目は作成日などの説明、2行目が見出し
    with EDINET_CODES_PATH.open(encoding="cp932", newline="") as file:
        rows = list(csv.reader(file))[2:]
    industries = {row[0]: row[10] for row in rows if row[10]}
    with ACQUISITIONS_PATH.open(encoding="utf-8-sig", newline="") as file:
        acquisitions = list(csv.DictReader(file))
    for row in acquisitions:
        row["consideration_yen"] = row["consideration_yen"] or None
    labeled = label_acquisitions(acquisitions, years_by_company, industries)
    with LABELED_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(labeled[0]))
        writer.writeheader()
        writer.writerows(labeled)
    reasons = defaultdict[str, int](int)
    for row in labeled:
        reasons[row["unlabeled_reason"] or "成否を付けた"] += 1
    print(f"台帳 {len(labeled)} 行：{dict(reasons)}：{LABELED_PATH}")


if __name__ == "__main__":
    main()
