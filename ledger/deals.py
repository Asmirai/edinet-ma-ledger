"""注記から抜き出した買収案件を扱うときの、共通の決まりと照合。

抜き出しそのものは ledger.claude_deals（Claude Code のサブエージェント）で行う。
ここには、抜き出し・台帳づくり・誤り率の採点のどれもが使う、注記の選び方・原文との照合・金額の換算を置く。
"""

import unicodedata
from typing import Any

# 「該当事項はありません」「連結財務諸表に同一の内容を記載」などの定型文は、どれも 80 字に届かない。
# 中身のある注記は、名称・企業結合日・取得原価を書くだけで 150 字を超える
MIN_SUBSTANTIVE_LENGTH = 150
YEN_PER_UNIT = {"円": 1, "千円": 1_000, "百万円": 1_000_000, "億円": 100_000_000}
TEXT_FIELDS = ("business", "acquisition_date", "legal_form", "voting_ratio")
AMOUNT_FIELDS = ("consideration", "goodwill", "assets_total", "proforma_sales")
ACQUISITION = "取得"


def is_substantive(note_text: str) -> bool:
    """定型文でなく、中身のある注記か。"""
    return len(_normalize(note_text)) >= MIN_SUBSTANTIVE_LENGTH


def notes_to_read(notes: list[dict[str, str]]) -> list[dict[str, str]]:
    """有報1件の注記のうち、読むものを選ぶ。連結の注記があれば連結だけにし、同じ文章は1つにする。"""
    substantive = [note for note in notes if is_substantive(note["text"])]
    consolidated = [note for note in substantive if "Consolidated" in note["element"]]
    chosen = consolidated or substantive
    # 同じ文章が別のコンテキストで2回入っていることがある
    unique: dict[str, dict[str, str]] = {}
    for note in chosen:
        unique.setdefault(note["text"], note)
    return list(unique.values())


def unverified_fields(deal: dict[str, Any], source_text: str) -> list[str]:
    """名称と金額のうち、原文にそのまま載っていない項目の名前を返す。空の項目は照合しない。"""
    source = _normalize(source_text)
    missing: list[str] = []
    for field in ("acquiree_name", *TEXT_FIELDS):
        value = deal.get(field)
        if value is not None and _normalize(str(value)) not in source:
            missing.append(field)
    for field in AMOUNT_FIELDS:
        amount = deal.get(field)
        if amount is not None and _normalize(str(amount["value"])) not in source:
            missing.append(field)
    return missing


def to_yen(value: str, unit: str) -> int | None:
    """書かれた数字と単位を円に直す。単位が知らないものか、数字でなければ None。"""
    if unit not in YEN_PER_UNIT:
        return None
    number = _normalize(value)
    sign = -1 if number[:1] in ("△", "▲", "-") else 1
    try:
        amount = float(number.lstrip("△▲-"))
    except ValueError:
        return None
    return sign * round(amount * YEN_PER_UNIT[unit])


def _normalize(text: str) -> str:
    """全角・半角をそろえ（NFKC）、空白とカンマを除く。照合と数字の読み取りで同じそろえ方を使う。"""
    return "".join(unicodedata.normalize("NFKC", text).split()).replace(",", "")
