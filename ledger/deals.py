"""「企業結合等関係」の注記の文章から、手元の LLM（Ollama）で買収案件を抜き出し、原文と照合する。

1つの注記に何件も書かれていると、8B のモデルは金額を取りこぼす。そこで二段に分ける。
一段目で案件の一覧（名前・種類・期）を出させ、二段目で「取得」の案件ごとに、その案件の部分だけを渡して詳細を聞く。
"""

import json
import unicodedata
from typing import Any, cast

import httpx

# 「該当事項はありません」「連結財務諸表に同一の内容を記載」などの定型文は、どれも 80 字に届かない。
# 中身のある注記は、名称・企業結合日・取得原価を書くだけで 150 字を超える
MIN_SUBSTANTIVE_LENGTH = 150
YEN_PER_UNIT = {"円": 1, "千円": 1_000, "百万円": 1_000_000, "億円": 100_000_000}
LISTED_FIELDS = ("acquiree_name", "kind", "period")
TEXT_FIELDS = ("business", "acquisition_date", "legal_form", "voting_ratio")
AMOUNT_FIELDS = ("consideration", "goodwill", "assets_total", "proforma_sales")
ACQUISITION = "取得"
PRIOR_PERIOD = "前期"
PRIOR_PERIOD_WORDS = ("前連結会計年度", "前事業年度")
# モデルが空の値を、null ではなく文字で返すことがある
EMPTY_VALUES = ("", "null", "none", "-", "－")
# 注記は長いもので 1 万字を超える。日本語はほぼ 1 字 1 トークンなので、既定の文脈長では切れる
OPTIONS = {"temperature": 0, "num_ctx": 16384}

LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "deals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "acquiree_name": {"type": "string"},
                    "kind": {"type": "string", "enum": [ACQUISITION, "共通支配下の取引", "その他"]},
                    "period": {"type": "string", "enum": ["当期", PRIOR_PERIOD]},
                },
                "required": list(LISTED_FIELDS),
            },
        }
    },
    "required": ["deals"],
}
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
DETAIL_SCHEMA = {
    "type": "object",
    "properties": {
        **{field: _NULLABLE_TEXT for field in TEXT_FIELDS},
        **{field: _NULLABLE_AMOUNT for field in AMOUNT_FIELDS},
    },
    "required": [*TEXT_FIELDS, *AMOUNT_FIELDS],
}

LIST_PROMPT = """\
有価証券報告書の「企業結合等関係」の注記に書かれた企業結合を、すべて列挙してください。

- acquiree_name：被取得企業（合併の消滅会社、取得した事業）の名称を、原文どおりに写します。
- kind：グループの外の会社の支配を新たに獲得したものは「取得」。完全子会社や子会社との合併、子会社株式の追加取得、親会社・兄弟会社との取引など、同じグループの中の再編は「共通支配下の取引」。事業分離や株式の売却などは「その他」。
- period：当連結会計年度（当事業年度）の案件は「当期」、前連結会計年度（前事業年度）の案件は「前期」。

注記：
"""
DETAIL_PROMPT = """\
有価証券報告書の「企業結合等関係」の注記のうち、被取得企業「{name}」の企業結合について、次の項目を原文から写してください。言い換え、計算、単位の換算はしません。書かれていない項目は null にします。

- business：事業の内容。acquisition_date：企業結合日。legal_form：企業結合の法的形式。voting_ratio：取得した議決権比率。
- 金額は、value に数字（カンマや△も原文どおり）、unit に単位（円・千円・百万円・億円）を入れます。「（単位：百万円）」のように表の前にまとめて書かれた単位は、その表の数字の単位です。
- consideration：取得原価。goodwill：発生したのれんの金額（負ののれんは null）。assets_total：受け入れた資産の合計。proforma_sales：企業結合が期首に完了したと仮定した場合の、売上高（売上収益）の概算額。

注記：
"""


def is_substantive(note_text: str) -> bool:
    """定型文でなく、LLM に渡す中身のある注記か。"""
    return len(_normalize(note_text)) >= MIN_SUBSTANTIVE_LENGTH


def extract_deals(client: httpx.Client, note_text: str, model: str) -> list[dict[str, Any]]:
    """注記の文章から案件の一覧を抜き出す。値は原文のままの文字列で返る。

    各案件の unverified には、名称と金額のうち、モデルに渡した原文に載っていない項目の名前が入る。
    """
    listed = cast(
        list[dict[str, Any]], _ask(client, model, LIST_PROMPT + note_text, LIST_SCHEMA)["deals"]
    )
    listed = clean_listed(listed, note_text)
    names = [str(deal["acquiree_name"]) for deal in listed]
    deals: list[dict[str, Any]] = []
    for index, deal in enumerate(listed):
        if deal["kind"] != ACQUISITION:
            empty = dict.fromkeys((*TEXT_FIELDS, *AMOUNT_FIELDS))
            deals.append(deal | empty | {"unverified": unverified_fields(deal, note_text)})
            continue
        section = section_for(note_text, names, index)
        prompt = DETAIL_PROMPT.format(name=deal["acquiree_name"]) + section
        details = _drop_empty(_ask(client, model, prompt, DETAIL_SCHEMA))
        detailed = deal | details
        deals.append(detailed | {"unverified": unverified_fields(detailed, section)})
    return deals


def clean_listed(listed: list[dict[str, Any]], note_text: str) -> list[dict[str, Any]]:
    """一段目の一覧から、作り話の疑いが強い案件を除く。

    前の期の記載が無い注記の「前期」の案件と、同じ名前・同じ期の2件目以降は、モデルの作り話だった。
    """
    has_prior = any(word in note_text for word in PRIOR_PERIOD_WORDS)
    seen: set[tuple[str, str]] = set()
    cleaned: list[dict[str, Any]] = []
    for deal in listed:
        key = (_normalize(str(deal["acquiree_name"])), str(deal["period"]))
        if key in seen or (deal["period"] == PRIOR_PERIOD and not has_prior):
            continue
        seen.add(key)
        cleaned.append(deal)
    return cleaned


def section_for(note_text: str, names: list[str], index: int) -> str:
    """index 番目の案件の部分。その名前が初めて出る所から、ほかの案件の名前が次に出る所の前まで。

    名前が原文に無ければ、注記の全文を返す。
    """
    starts = [note_text.find(name) for name in names]
    start = starts[index]
    if start == -1:
        return note_text
    end = min((other for other in starts if other > start), default=len(note_text))
    return note_text[start:end]


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


def _ask(client: httpx.Client, model: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "format": schema,
            "stream": False,
            "think": False,
            "options": OPTIONS,
        },
    )
    response.raise_for_status()
    return cast(dict[str, Any], json.loads(response.json()["message"]["content"]))


def _drop_empty(details: dict[str, Any]) -> dict[str, Any]:
    """「null」などの文字で返った空の値を None にする。"""
    cleaned: dict[str, Any] = {}
    for field in TEXT_FIELDS:
        value = details.get(field)
        cleaned[field] = (
            None if value is None or _normalize(str(value)).lower() in EMPTY_VALUES else value
        )
    for field in AMOUNT_FIELDS:
        amount = details.get(field)
        is_empty = amount is None or _normalize(str(amount["value"])).lower() in EMPTY_VALUES
        cleaned[field] = None if is_empty else amount
    return cleaned


def _normalize(text: str) -> str:
    """全角・半角をそろえ（NFKC）、空白とカンマを除く。照合と数字の読み取りで同じそろえ方を使う。"""
    return "".join(unicodedata.normalize("NFKC", text).split()).replace(",", "")
