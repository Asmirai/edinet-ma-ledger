from ledger.deals import is_substantive, notes_to_read, to_yen, unverified_fields

NOTE = (
    "（企業結合等関係）取得による企業結合（１）企業結合の概要① 被取得企業の名称及びその事業の内容"
    "被取得企業の名称：株式会社アドウィル事業の内容：コミュニケーションデザインサービス"
    "③ 企業結合日2024年５月８日④ 企業結合の法的形式株式取得⑥ 取得した議決権比率100％"
    "取得原価 1,410 百万円 発生したのれんの金額１２１百万円"
)

DETAILS: dict[str, object] = {
    "business": "コミュニケーションデザインサービス",
    "acquisition_date": "2024年５月８日",
    "legal_form": "株式取得",
    "voting_ratio": "100％",
    "consideration": {"value": "1,410", "unit": "百万円"},
    "goodwill": {"value": "１２１", "unit": "百万円"},
    "assets_total": None,
    "proforma_sales": None,
}


def _listed(name: str, kind: str = "取得", period: str = "当期") -> dict[str, str]:
    return {"acquiree_name": name, "kind": kind, "period": period}


def _deal(**changes: object) -> dict[str, object]:
    return _listed("株式会社アドウィル") | DETAILS | changes


def 百万円を円に直す() -> None:
    assert to_yen("1,410", "百万円") == 1_410_000_000


def 全角の数字と千円も円に直す() -> None:
    assert to_yen("１，４１０", "千円") == 1_410_000


def 億円と円も円に直す() -> None:
    assert to_yen("3.5", "億円") == 350_000_000
    assert to_yen("12,000", "円") == 12_000


def 三角のついた数字は負にする() -> None:
    assert to_yen("△823", "百万円") == -823_000_000


def 知らない単位や数字でない値は円に直さない() -> None:
    assert to_yen("1,410", "ドル") is None
    assert to_yen("非開示", "百万円") is None


def 原文に全部載っていれば照合できない項目は無い() -> None:
    assert unverified_fields(_deal(), NOTE) == []


def 原文に無い名称と金額を照合できない項目として返す() -> None:
    deal = _deal(
        acquiree_name="株式会社ファーストライフ", goodwill={"value": "450", "unit": "百万円"}
    )

    assert unverified_fields(deal, NOTE) == ["acquiree_name", "goodwill"]


def 空の項目は照合しない() -> None:
    assert unverified_fields(_deal(business=None, consideration=None), NOTE) == []


def 該当事項なしの定型文は中身のある注記としない() -> None:
    assert not is_substantive("（企業結合等関係）　該当事項はありません。 ")


def 名称や金額を書いた注記は中身のある注記とする() -> None:
    assert is_substantive(NOTE)


def 連結の注記があれば連結だけを読み同じ文章は1つにする() -> None:
    notes = [
        {"element": "NotesBusinessCombinationsConsolidated", "text": NOTE},
        {"element": "NotesBusinessCombinationsConsolidated", "text": NOTE},
        {"element": "NotesBusinessCombinations", "text": NOTE + "（単体）"},
        {"element": "NotesBusinessCombinationsConsolidated", "text": "該当事項はありません。"},
    ]

    assert notes_to_read(notes) == [notes[0]]
