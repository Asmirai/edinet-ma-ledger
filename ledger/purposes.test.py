from pathlib import Path

import pytest

from ledger.chunks import write_chunks
from ledger.purposes import consideration_kind, purpose_items, read_purposes, reason_excerpt

NOTE = (
    "（企業結合等関係）取得による企業結合（１）企業結合の概要①被取得企業の名称及びその事業の内容"
    "被取得企業の名称　株式会社アドウィル　事業の内容　広告②企業結合を行った主な理由　当社の広告事業と"
    "アドウィルの顧客基盤を合わせ、首都圏での販売を広げるためであります。③企業結合日　2024年５月８日"
)


def 相手の名前の後の主な理由の部分を切り出す() -> None:
    excerpt = reason_excerpt(NOTE, "株式会社アドウィル")

    assert excerpt is not None
    assert excerpt.startswith("当社の広告事業とアドウィルの顧客基盤を合わせ")
    assert len(excerpt) <= 300


def 主な理由が見つからなければ空にする() -> None:
    assert reason_excerpt("株式会社アドウィルを買った。", "株式会社アドウィル") is None
    assert reason_excerpt(NOTE, "株式会社ほかの会社") is None


def 法的形式から対価の種類を読む() -> None:
    assert consideration_kind("現金を対価とする株式取得") == "現金"
    assert consideration_kind("株式交換") == "株式"
    assert consideration_kind("当社株式を対価とする株式取得") == "株式"
    assert consideration_kind("現金及び当社株式を対価とする株式取得") == "現金と株式"
    assert consideration_kind("株式取得") == "不明"


def 成否を付けられる年の主の行から目的を聞く入力を作る() -> None:
    rows = [
        {
            "deal_id": "1",
            "edinetCode": "E1",
            "filerName": "買い手株式会社",
            "industry": "サービス業",
            "acquiree_name": "株式会社アドウィル",
            "business": "広告",
            "docIDs": "S1",
            "primary": "True",
            "base_year": "2021",
        },
        {"deal_id": "2", "edinetCode": "E1", "primary": "False", "base_year": "2021"},
        {"deal_id": "3", "edinetCode": "E1", "primary": "True", "base_year": "2024"},
    ]

    items = purpose_items(rows, {"S1": [NOTE]}, last_base_year=2022)

    assert [item["key"] for item in items] == ["1-E1"]
    assert items[0]["acquirer_industry"] == "サービス業"
    assert items[0]["reason"].startswith("当社の広告事業と")


def 分類の一覧に無い値が出力にあれば止める(tmp_path: Path) -> None:
    write_chunks([{"key": "1-E1"}], tmp_path, 200)
    (tmp_path / "out" / "chunk_000.json").write_text(
        '[{"key": "1-E1", "purpose": "なんとなく", "relatedness": "同じ事業"}]', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="なんとなく"):
        read_purposes(tmp_path)


def 分類させた業種の関係を記事とCSVの名前に読み替える(tmp_path: Path) -> None:
    write_chunks([{"key": "1-E1"}, {"key": "2-E1"}, {"key": "3-E1"}], tmp_path, 200)
    (tmp_path / "out" / "chunk_000.json").write_text(
        '[{"key": "1-E1", "purpose": "同じ事業の拡大", "relatedness": "同じ事業"},'
        ' {"key": "2-E1", "purpose": "同じ事業の拡大", "relatedness": "近い事業"},'
        ' {"key": "3-E1", "purpose": "新しい事業への参入", "relatedness": "遠い事業"}]',
        encoding="utf-8",
    )

    purposes = read_purposes(tmp_path)

    assert [purposes[k][1] for k in ("1-E1", "2-E1", "3-E1")] == ["同業種", "関連業種", "異業種"]
