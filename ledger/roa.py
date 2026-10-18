"""買収した会社の ROA が買収の前後でどう変わったかを有報から出し、同業と比べて成否を付ける。

ROA は、親会社株主に帰属する当期純利益 ÷ 期末の総資産。有報の「主要な経営指標等の推移」には5期分が載っている。
企業結合日を含む事業年度を基準の期とし、買収後3期の ROA の平均から買収前の期の ROA を引いた差（ΔROA）を出す。
同じ業種・同じ基準の年の会社の ΔROA の中央値を引いたものを超過 ΔROA とし、0 より大きければ成功とする。

使い方：uv run python -m ledger.roa
（data/documents の有報の ZIP と、data/codelist/EdinetcodeDlInfo.csv（EDINET コードリスト）を先に置いておく）
"""

import bisect
import calendar
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
# 連結の値は、会計基準ごとに要素の名前が違う。同じ期に2つの基準があれば、前にあるものを使う。
# IFRS に移った最初の有報には、前の期が日本基準と IFRS の両方で載る。のれんを償却しない IFRS にそろえる
CONSOLIDATED = (
    (
        "IFRS",
        (
            "jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
            "jpcrp_cor:ProfitLossIFRSSummaryOfBusinessResults",
        ),
        "jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults",
    ),
    (
        "米国基準",
        ("jpcrp_cor:NetIncomeLossAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",),
        "jpcrp_cor:TotalAssetsUSGAAPSummaryOfBusinessResults",
    ),
    (
        "日本基準",
        (
            "jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
            "jpcrp_cor:NetIncomeLossSummaryOfBusinessResults",
        ),
        "jpcrp_cor:TotalAssetsSummaryOfBusinessResults",
    ),
)
# 連結が無い会社（子会社を持たない会社）は単体の値を使う
SINGLE = (
    "単体",
    ("jpcrp_cor:NetIncomeLossSummaryOfBusinessResults",),
    "jpcrp_cor:TotalAssetsSummaryOfBusinessResults",
)
SINGLE_MEMBER = "_NonConsolidatedMember"
# 「CurrentYearDuration」「Prior3YearInstant」など。何期前かを読む
CONTEXT = re.compile(r"(CurrentYear|Prior(\d)Year)(Duration|Instant)")
YEARS_AFTER = 3


def read_summary(archive: bytes) -> dict[int, tuple[int, int, str]]:
    """有報の CSV の ZIP から、何期前か → （純利益、総資産、基準）を読む。

    基準は「IFRS」「米国基準」「日本基準」（ここまで連結）か「単体」。連結を優先する。
    純利益か総資産のどちらかが欠けた期は入れない。
    """
    values: dict[tuple[bool, int, str], int] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        for name in sorted(files.namelist()):
            if not name.startswith(REPORT_CSV_PREFIX):
                continue
            # UTF-16（BOM 付き）のタブ区切り。1行目は見出し
            text = files.read(name).decode("utf-16")
            for row in list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]:
                element, context, value = row[0], row[2], row[8]
                match = CONTEXT.fullmatch(context.removesuffix(SINGLE_MEMBER))
                if match is not None and re.fullmatch(r"-?\d+", value):
                    single = context.endswith(SINGLE_MEMBER)
                    values[(single, int(match.group(2) or 0), element)] = int(value)
    summary: dict[int, tuple[int, int, str]] = {}
    for years_ago in sorted({key[1] for key in values}):
        for single, (basis, profit_elements, assets_element) in [
            *((False, entry) for entry in CONSOLIDATED),
            (True, SINGLE),
        ]:
            profits = [values.get((single, years_ago, e)) for e in profit_elements]
            profit = next((p for p in profits if p is not None), None)
            assets = values.get((single, years_ago, assets_element))
            if profit is not None and assets is not None:
                summary[years_ago] = (profit, assets, basis)
                break
    return summary


def company_years(
    reports: list[tuple[date, dict[int, tuple[int, int, str]]]],
) -> dict[date, tuple[int, int, str]]:
    """有報の決算日と5期分の値から、各期の決算日 → （純利益、総資産、基準）を作る。同じ期は後の有報の値を使う。

    前の期の決算日は「決算日の年 − 何期前か」で出す。決算期を変えた会社ではこれがずれるので、
    各有報の当期の値だけを使う。
    """
    month_days = {
        (end.month, min(end.day, 28) if end.month == 2 else end.day) for end, _ in reports
    }
    changed = len(month_days) > 1
    years: dict[date, tuple[int, int, str]] = {}
    for period_end, summary in sorted(reports):
        for years_ago, values in summary.items():
            if changed and years_ago > 0:
                continue
            year = period_end.year - years_ago
            # 2月末決算は年によって28日と29日がある
            day = calendar.monthrange(year, 2)[1] if period_end.month == 2 else period_end.day
            years[period_end.replace(year=year, day=day)] = values
    return years


def _window(
    years: dict[date, tuple[int, int, str]], base_year: int
) -> tuple[float, list[float]] | str:
    """買収前の期の ROA と、買収後3期の ROA。そろわなければ理由を返す。期は決算日の年で数える。"""
    wanted = range(base_year - 1, base_year + YEARS_AFTER + 1)
    if base_year + YEARS_AFTER > max(end.year for end in years):
        return "買収後3期の有報がまだ無い"
    ends = {
        year: [end for end in years if end.year == year] for year in wanted if year != base_year
    }
    if any(not found for found in ends.values()):
        return "前後の ROA が欠けている"
    window = [end for found in ends.values() for end in found]
    if len(window) != len(ends) or len({(end.month, end.day) for end in window}) > 1:
        return "決算期の変更"
    if len({years[end][2] for end in window}) > 1:
        return "会計基準か連結・単体が変わった"
    if any(years[end][1] <= 0 for end in window):
        return "前後の ROA が欠けている"
    roas = [years[end][0] / years[end][1] for end in sorted(window)]
    return roas[0], roas[1:]


def delta_roa(years: dict[date, tuple[int, int, str]], base_year: int) -> float | None:
    """買収後3期の ROA の平均から、買収前の期の ROA を引く。そろわなければ None。"""
    window = _window(years, base_year)
    if isinstance(window, str):
        return None
    before, after = window
    return statistics.mean(after) - before


def label_acquisitions(
    rows: list[dict[str, Any]],
    years_by_company: dict[str, dict[date, tuple[int, int, str]]],
    industries: dict[str, str],
) -> list[dict[str, Any]]:
    """台帳の行に、基準の期、ΔROA、超過 ΔROA、成否、買収の大きさ、主の行かどうか、空にした理由を付ける。

    親会社と子会社が同じ案件を載せたときは、買収前の総資産が小さいほう（直接買った会社に近いほう）を主の行にする。
    """
    deltas: defaultdict[tuple[str, int], list[float]] = defaultdict(list)
    befores: defaultdict[tuple[str, int], list[float]] = defaultdict(list)
    # 買収前の ROA でそろえて比べるため、基準の年ごとに全社の（買収前の ROA、ΔROA）を集める
    pairs: defaultdict[int, list[tuple[float, float]]] = defaultdict(list)
    for code, years in years_by_company.items():
        if code not in industries:
            continue
        for base_year in {end.year + 1 for end in years}:  # 買収前の期が base_year − 1
            window = _window(years, base_year)
            if isinstance(window, str):
                continue
            delta = statistics.mean(window[1]) - window[0]
            deltas[(industries[code], base_year)].append(delta)
            befores[(industries[code], base_year)].append(window[0])
            pairs[base_year].append((window[0], delta))
    medians = {key: statistics.median(values) for key, values in deltas.items()}
    before_medians = {key: statistics.median(values) for key, values in befores.items()}
    # 買収前の ROA の10分位の境目と、帯ごとの ΔROA の中央値
    cuts = {
        year: statistics.quantiles([b for b, _ in found], n=10, method="inclusive")
        for year, found in pairs.items()
        if len(found) >= 2
    }
    bands: defaultdict[tuple[int, int], list[float]] = defaultdict(list)
    for year, found in pairs.items():
        for before, delta in found:
            if year in cuts:
                bands[(year, bisect.bisect_right(cuts[year], before))].append(delta)

    labeled: list[dict[str, Any]] = []
    for row in rows:
        result: dict[str, Any] = dict.fromkeys(
            (
                "base_year",
                "roa_before",
                "excess_roa_before",
                "delta_roa",
                "excess_delta_roa",
                "matched_excess_delta_roa",
                "success",
                "size_ratio",
                "assets_before",
            )
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
            later = [end for end in sorted(years) if end >= acquired_on]
            if later and (later[0] - acquired_on).days <= 366:
                base_year = later[0].year
            else:
                # 取得日の後の決算日がまだ無いときは、最後の決算日の月日から出す
                last_end = max(years)
                day = (
                    calendar.monthrange(acquired_on.year, 2)[1]
                    if last_end.month == 2
                    else last_end.day
                )
                base_end = date(acquired_on.year, last_end.month, day)
                base_year = base_end.year if base_end >= acquired_on else base_end.year + 1
            result["base_year"] = base_year
            before = next((v for end, v in years.items() if end.year == base_year - 1), None)
            if before is not None:
                result["assets_before"] = before[1]
                if row["consideration_yen"] is not None and before[1] > 0:
                    result["size_ratio"] = int(row["consideration_yen"]) / before[1]
            window = _window(years, base_year)
            median = medians.get((result["industry"] or "", base_year))
            if isinstance(window, str):
                result["unlabeled_reason"] = window
            elif median is None:
                result["unlabeled_reason"] = "業種が無い"
            else:
                result["delta_roa"] = statistics.mean(window[1]) - window[0]
                result["excess_delta_roa"] = result["delta_roa"] - median
                result["success"] = result["excess_delta_roa"] > 0
                result["unlabeled_reason"] = None
                result["roa_before"] = window[0]
                industry_before = before_medians[(result["industry"] or "", base_year)]
                result["excess_roa_before"] = window[0] - industry_before
                if base_year in cuts:
                    band = bisect.bisect_right(cuts[base_year], window[0])
                    matched = statistics.median(bands[(base_year, band)])
                    result["matched_excess_delta_roa"] = result["delta_roa"] - matched
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
    saved: dict[str, Any] = {}
    if FINANCIALS_PATH.exists():
        saved = cast(dict[str, Any], json.loads(FINANCIALS_PATH.read_text(encoding="utf-8")))
    # 3万冊の ZIP を読むのに時間がかかるので、読んだ値を残し、まだ読んでいない有報だけを読む
    unread = [path for path in sorted(NOTES_DIR.glob("*.json")) if path.stem not in saved]
    for notes_path in unread:
        report = json.loads(notes_path.read_text(encoding="utf-8"))
        archive = (DOCUMENTS_DIR / f"{report['docID']}.zip").read_bytes()
        saved[report["docID"]] = {
            "edinetCode": report["edinetCode"],
            "periodEnd": report["periodEnd"],
            "summary": read_summary(archive),
        }
    if unread:
        FINANCIALS_PATH.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    reports_by_company: defaultdict[str, list[tuple[date, dict[int, tuple[int, int, str]]]]] = (
        defaultdict(list)
    )
    for report in cast(list[dict[str, Any]], list(saved.values())):
        values = cast(dict[str, list[Any]], report["summary"])
        summary = {int(k): (int(v[0]), int(v[1]), str(v[2])) for k, v in values.items()}
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
