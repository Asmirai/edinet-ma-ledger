"""「企業結合等関係」の注記の文章から、手元の LLM（Ollama）で買収案件を抜き出し、原文と照合する。"""

import json
import unicodedata
from typing import Any, cast

import httpx

# 「該当事項はありません」「連結財務諸表に同一の内容を記載」などの定型文は、どれも 80 字に届かない。
# 中身のある注記は、名称・企業結合日・取得原価を書くだけで 150 字を超える
MIN_SUBSTANTIVE_LENGTH = 150
YEN_PER_UNIT = {"円": 1, "千円": 1_000, "百万円": 1_000_000, "億円": 100_000_000}
TEXT_FIELDS = ("acquiree_name", "business", "acquisition_date", "legal_form", "voting_ratio")
AMOUNT_FIELDS = ("consideration", "goodwill", "assets_total", "proforma_sales")
# 注記は長いもので 1 万字を超える。日本語はほぼ 1 字 1 トークンなので、既定の文脈長では切れる
OPTIONS = {"temperature": 0, "num_ctx": 16384}

_NULLABLE_TEXT = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_NULLABLE_AMOUNT = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "unit": {"type": "string", "enum": list(YEN_PER_UNIT)},
            },
            "required": ["value", "unit"],
        },
        {"type": "null"},
    ]
}
SCHEMA = {
    "type": "object",
    "properties": {
        "deals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["取得", "共通支配下の取引", "その他"]},
                    "period": {"type": "string", "enum": ["当期", "前期"]},
                    **{field: _NULLABLE_TEXT for field in TEXT_FIELDS},
                    **{field: _NULLABLE_AMOUNT for field in AMOUNT_FIELDS},
                },
                "required": ["kind", "period", *TEXT_FIELDS, *AMOUNT_FIELDS],
            },
        }
    },
    "required": ["deals"],
}

INSTRUCTIONS = """\
有価証券報告書の「企業結合等関係」の注記から、企業結合を1件ずつ抜き出してください。

- 注記に書かれた企業結合ごとに1件にします。被取得企業が複数あれば、複数件にします。
- kind：他社の支配を獲得した企業結合は「取得」。同じグループの中の再編（共通支配下の取引等）は「共通支配下の取引」。事業分離や子会社株式の売却など、それ以外は「その他」。
- period：当連結会計年度（当事業年度）の案件は「当期」、前連結会計年度（前事業年度）の案件は「前期」。
- 値は、原文に書かれている文字列をそのまま写します。言い換え、計算、単位の換算はしません。書かれていない項目は null にします。
- acquiree_name：被取得企業（または取得した事業）の名称。business：その事業の内容。acquisition_date：企業結合日。legal_form：企業結合の法的形式。voting_ratio：取得した議決権比率。
- 金額は、value に数字（カンマや△も原文どおり）、unit に単位（円・千円・百万円・億円）を入れます。「（単位：百万円）」のように表の前にまとめて書かれた単位は、その表の数字の単位です。
- consideration：取得原価（取得の対価の合計）。goodwill：発生したのれんの金額（負ののれんは null）。assets_total：企業結合日に受け入れた資産の合計。proforma_sales：企業結合が期首に完了したと仮定した場合の、売上高（売上収益）の概算額。

注記：
"""


def is_substantive(note_text: str) -> bool:
    """定型文でなく、LLM に渡す中身のある注記か。"""
    return len(_normalize(note_text)) >= MIN_SUBSTANTIVE_LENGTH


def extract_deals(client: httpx.Client, note_text: str, model: str) -> list[dict[str, Any]]:
    """注記の文章から、案件の一覧を LLM に抜き出させる。値は原文のままの文字列で返る。"""
    response = client.post(
        "/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": INSTRUCTIONS + note_text}],
            "format": SCHEMA,
            "stream": False,
            "think": False,
            "options": OPTIONS,
        },
    )
    response.raise_for_status()
    content = cast(str, response.json()["message"]["content"])
    return cast(list[dict[str, Any]], json.loads(content)["deals"])


def unverified_fields(deal: dict[str, Any], note_text: str) -> list[str]:
    """名称と金額のうち、原文にそのまま載っていない項目の名前を返す。空の項目は照合しない。"""
    source = _normalize(note_text)
    missing: list[str] = []
    for field in TEXT_FIELDS:
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
