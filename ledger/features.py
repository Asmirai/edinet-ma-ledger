"""台帳の1行（CSV の文字列）から、分析とモデルに使う列を出す。

ledger.factors（要因ごとの集計）、ledger.predict（ROA の予測）、ledger.impairments、ledger.dataset が共通で使う。
"""

import re
import unicodedata
from datetime import date, timedelta
from typing import Any

from ledger.industries import INDUSTRIES

# のれんは取得原価から受け入れた純資産を引いた額なので、債務超過の会社を買えば取得原価を超える。
# それでも 1.5 倍を超えるものは、取得原価の単位（千円・百万円）の読み違いが多かった
MAX_GOODWILL_RATIO = 1.5
PRIOR_DAYS = 730


def _number(text: str) -> float | None:
    return float(text) if text else None


def deal_features(row: dict[str, str], acquiree_industry: str | None) -> dict[str, Any]:
    """台帳の1行（CSV の文字列）から、分析に使う列を出す。分からない値は None。

    のれんの割合は のれん ÷ 取得原価。のれんが書かれていない買収（負ののれんか記載の省略）と、
    割合が 1.5 を超える買収（単位の読み違いが多い）は None にする。
    """
    consideration = _number(row["consideration_yen"])
    goodwill = _number(row["goodwill_yen"])
    goodwill_ratio = None
    if goodwill is not None and consideration is not None and consideration > 0:
        ratio = goodwill / consideration
        goodwill_ratio = ratio if ratio <= MAX_GOODWILL_RATIO else None
    matched = _number(row["matched_excess_delta_roa"])
    known_industries = acquiree_industry is not None and row["industry"] in INDUSTRIES
    return {
        "edinetCode": row["edinetCode"],
        "industry": row["industry"],
        "base_year": int(row["base_year"]),
        "success": None if not row["success"] else row["success"] == "True",
        "matched_success": None if matched is None else matched > 0,
        "delta_roa": _number(row["delta_roa"]),
        "excess_delta_roa": _number(row["excess_delta_roa"]),
        "matched_excess_delta_roa": matched,
        "excess_roa_before": _number(row["excess_roa_before"]),
        "size_ratio": _number(row["size_ratio"]),
        "goodwill_ratio": goodwill_ratio,
        "same_industry": acquiree_industry == row["industry"] if known_industries else None,
    }


def legal_form_kind(text: str) -> str:
    """有報に書かれた法的形式を、合併・株式交換・事業譲受・株式取得・その他・不明にまとめる。"""
    if not text:
        return "不明"
    if "合併" in text:
        return "合併"
    if "株式交換" in text:
        return "株式交換"
    if "事業" in text or "分割" in text:
        return "事業譲受"
    if "株式" in text or "持分" in text or "出資" in text:
        return "株式取得"
    return "その他"


def voting_number(text: str) -> float | None:
    """議決権比率の数字を読む。「取得後」があればその後ろの最初の数字、無ければ最初の数字。

    後ろに書き添えた間接保有の比率や、2社目の比率は読まない。
    """
    normalized = unicodedata.normalize("NFKC", text)
    after = normalized.split("取得後", 1)[-1]
    numbers = re.findall(r"\d+(?:\.\d+)?", after)
    return float(numbers[0]) if numbers else None


def recent_deals(dates: list[date], acquired_on: date, ledger_start: date) -> int | None:
    """企業結合日の前の2年に、同じ会社が買った件数。台帳の始まりから2年たっていなければ None。"""
    window_start = acquired_on - timedelta(days=PRIOR_DAYS)
    if window_start < ledger_start:
        return None
    return sum(window_start <= d < acquired_on for d in dates)
