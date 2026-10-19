"""台帳・のれんの減損・買収の目的・買われた会社の業種・買い手の財務を、買収1件1行の表にまとめる。

ledger.factors（要因ごとの集計）と ledger.figures（記事の図）がこの表を使う。
data の下に、roa・industries・impairments・purposes の結果を先に用意しておく。
"""

import csv
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, cast

from ledger.features import deal_features, legal_form_kind, recent_deals, voting_number
from ledger.impairments import IMPAIRMENTS_PATH
from ledger.industries import INDUSTRIES_DIR, read_industries
from ledger.notes import DOCUMENTS_DIR, NOTES_DIR
from ledger.profiles import acquirer_profile
from ledger.purposes import LAST_BASE_YEAR, consideration_kind, read_purposes
from ledger.roa import FINANCIALS_PATH, LABELED_PATH, company_years

PURPOSES_DIR = Path("data/purposes")
PROFILES_PATH = Path("data/profiles.json")
BIG_DEAL = 0.1
# 危険信号の境目。減損した割合の表で、いちばん高い四分位の境目に当たる値
HIGH_GOODWILL_RATIO = 0.84
HIGH_EQUITY_RATIO = 0.65
LARGE_DEAL = 0.05
PROFILE_FIELDS = ("sales_growth", "equity_ratio", "cash_ratio", "ocf_ratio")


def deal_table(
    rows: list[dict[str, str]],
    acquiree_industries: dict[str, str | None],
    purposes: dict[str, tuple[str, str]],
    impairments: dict[str, bool],
    profiles: dict[str, dict[str, float | None]],
    bases: dict[str, str],
) -> list[dict[str, Any]]:
    """成否を付けられる年の主の行について、列と2つの物差しを1行にまとめる。分からない値は None。"""
    dates: defaultdict[str, list[date]] = defaultdict(list)
    for row in rows:
        if row["acquisition_date"]:
            dates[row["edinetCode"]].append(date.fromisoformat(row["acquisition_date"]))
    ledger_start = min(d for found in dates.values() for d in found)
    deals: list[dict[str, Any]] = []
    for row in rows:
        if (
            row["primary"] != "True"
            or not row["acquisition_date"]
            or not row["base_year"]
            or int(row["base_year"]) > LAST_BASE_YEAR
        ):
            continue
        key = f"{row['deal_id']}-{row['edinetCode']}"
        acquiree_industry = acquiree_industries.get(key)
        features = deal_features(row, acquiree_industry)
        size, same = features["size_ratio"], features["same_industry"]
        purpose, relatedness = purposes.get(key, (None, None))
        profile = profiles.get(key, {})
        deals.append(
            {
                "key": key,
                "edinetCode": row["edinetCode"],
                "base_year": features["base_year"],
                "impaired": impairments.get(key),
                "matched_success": features["matched_success"],
                **{f"delta_roa_{k}": _number(row[f"delta_roa_{k}"]) for k in (1, 2, 3)},
                "roa_success": features["matched_success"]
                if size is not None and size >= BIG_DEAL
                else None,
                "same_industry": None if same is None else float(same),
                "goodwill_ratio": features["goodwill_ratio"],
                "size_ratio": size,
                "log_size_ratio": math.log(size) if size is not None and size > 0 else None,
                "excess_roa_before": features["excess_roa_before"],
                "voting_ratio": voting_number(row["voting_ratio"]),
                "prior_deals": recent_deals(
                    dates[row["edinetCode"]],
                    date.fromisoformat(row["acquisition_date"]),
                    ledger_start,
                ),
                "industry": row["industry"],
                "basis": bases.get(key),
                "acquiree_industry": acquiree_industry,
                "legal_form": legal_form_kind(row["legal_form"]),
                "consideration": consideration_kind(row["legal_form"]),
                "purpose": purpose,
                "relatedness": relatedness,
            }
            | {field: profile.get(field) for field in PROFILE_FIELDS}
        )
    return [deal | {"risk_count": risk_count(deal)} for deal in deals]


def risk_count(deal: dict[str, Any]) -> int | None:
    """減損が多かった4つの特徴のうち、いくつ当てはまるか。どれかが分からなければ None。

    のれんの割合が84%以上、遠い事業、買い手の自己資本比率が65%以上、取得原価が総資産の5%以上。
    """
    values = (deal["goodwill_ratio"], deal["relatedness"], deal["equity_ratio"], deal["size_ratio"])
    if any(value is None for value in values):
        return None
    return sum(
        (
            deal["goodwill_ratio"] >= HIGH_GOODWILL_RATIO,
            deal["relatedness"] == "遠い事業",
            deal["equity_ratio"] >= HIGH_EQUITY_RATIO,
            deal["size_ratio"] >= LARGE_DEAL,
        )
    )


def _number(text: str) -> float | None:
    return float(text) if text else None


def load_deals() -> list[dict[str, Any]]:
    """data の下の台帳・減損・目的・業種・買い手の財務から、deal_table の行を作る。"""
    with LABELED_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    with IMPAIRMENTS_PATH.open(encoding="utf-8-sig", newline="") as file:
        impairments = {
            r["key"]: r["impaired"] == "True" for r in csv.DictReader(file) if r["impaired"]
        }
    return deal_table(
        rows,
        read_industries(INDUSTRIES_DIR),
        read_purposes(PURPOSES_DIR),
        impairments,
        _profiles(rows),
        _bases(),
    )


def _profiles(rows: list[dict[str, str]]) -> dict[str, dict[str, float | None]]:
    """案件ごとに、買い手の買収直前の期（基準の年の前年が決算日の年）の有報から財務を読む。読んだ値は残す。"""
    saved: dict[str, dict[str, float | None]] = {}
    if PROFILES_PATH.exists():
        saved = cast(
            dict[str, dict[str, float | None]],
            json.loads(PROFILES_PATH.read_text(encoding="utf-8")),
        )
    reports: dict[tuple[str, int], str] = {}
    for notes_path in NOTES_DIR.glob("*.json"):
        report = cast(dict[str, Any], json.loads(notes_path.read_text(encoding="utf-8")))
        reports[(report["edinetCode"], date.fromisoformat(report["periodEnd"]).year)] = report[
            "docID"
        ]
    for row in rows:
        key = f"{row['deal_id']}-{row['edinetCode']}"
        if key in saved or row["primary"] != "True" or not row["base_year"]:
            continue
        doc_id = reports.get((row["edinetCode"], int(row["base_year"]) - 1))
        if doc_id is not None:
            saved[key] = acquirer_profile((DOCUMENTS_DIR / f"{doc_id}.zip").read_bytes())
    PROFILES_PATH.write_text(json.dumps(saved), encoding="utf-8")
    return saved


def _bases() -> dict[str, str]:
    """案件ごとの、買い手の買収前の期（基準の年の前年）の会計基準（日本基準・IFRS・米国基準・単体）。

    ledger.roa と同じ company_years で期を並べ、ROA や買収の大きさの分母と同じ期の基準を取る。
    """
    with LABELED_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = [r for r in csv.DictReader(file) if r["base_year"]]
    saved = cast(dict[str, dict[str, Any]], json.loads(FINANCIALS_PATH.read_text(encoding="utf-8")))
    reports: defaultdict[str, list[tuple[date, dict[int, tuple[int, int, str]]]]] = defaultdict(
        list
    )
    for report in saved.values():
        values = cast(dict[str, list[Any]], report["summary"])
        summary = {int(k): (int(v[0]), int(v[1]), str(v[2])) for k, v in values.items()}
        reports[str(report["edinetCode"])].append(
            (date.fromisoformat(report["periodEnd"]), summary)
        )
    years = {code: company_years(found) for code, found in reports.items()}
    bases: dict[str, str] = {}
    for row in rows:
        before = int(row["base_year"]) - 1
        found = years.get(row["edinetCode"], {})
        basis = next((v[2] for end, v in found.items() if end.year == before), None)
        if basis is not None:
            bases[f"{row['deal_id']}-{row['edinetCode']}"] = basis
    return bases
