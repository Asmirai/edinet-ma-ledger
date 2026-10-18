import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from ledger.impairments import (
    candidate_items,
    core_name,
    impairment_passages,
    label_impairments,
    read_impairments,
    split_candidates,
)

HEADER = [
    "要素ID",
    "項目名",
    "コンテキストID",
    "相対年度",
    "連結・個別",
    "期間・時点",
    "ユニットID",
    "単位",
    "値",
]


def _archive(blocks: list[tuple[str, str]]) -> bytes:
    lines = ["\t".join(f'"{cell}"' for cell in HEADER)]
    for element, text in blocks:
        cells = [element, "項目", "CurrentYearDuration", "当期", "連結", "期間", "－", "－", text]
        lines.append("\t".join(f'"{cell}"' for cell in cells))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XBRL_TO_CSV/jpcrp030000-asr.csv", "\r\n".join(lines).encode("utf-16"))
    return buffer.getvalue()


def 会社の種類と今の名前の書き添えを除いた名前で探す() -> None:
    assert core_name("株式会社アドウィル（現株式会社ＡＤＷ）") == "アドウィル"
    assert core_name("㈱ＤＧ Life Design") == "DG Life Design"


def 減損を含む文章から相手の名前の前後を抜き出す() -> None:
    text = (
        "前置き" * 200
        + "当連結会計年度において、株式会社アドウィルに係るのれんの減損損失120百万円を計上しました。"
    )
    archive = _archive(
        [
            ("jpcrp_cor:NotesRegardingImpairmentLossTextBlock", text),
            ("jpcrp_cor:NotesSegmentInformationTextBlock", "アドウィルの売上は増えました。"),
        ]
    )

    [passage] = impairment_passages(archive, "株式会社アドウィル")

    assert "アドウィルに係るのれんの減損損失120百万円" in passage
    assert len(passage) < 500


def 名前が短すぎれば探さない() -> None:
    archive = _archive([("jpcrp_cor:NotesTextBlock", "ABの減損")])

    assert impairment_passages(archive, "AB") == []


def _deal(**changes: object) -> dict[str, object]:
    deal: dict[str, object] = {
        "key": "1-E1",
        "edinetCode": "E1",
        "filerName": "買い手株式会社",
        "acquiree_name": "株式会社アドウィル",
        "base_year": 2021,
    }
    return deal | changes


def 基準の期から3期後までの有報で拾えた案件だけを候補にする() -> None:
    reports = {
        "E1": [
            (date(2020, 3, 31), "S0"),
            (date(2021, 3, 31), "S1"),
            (date(2023, 3, 31), "S3"),
            (date(2025, 3, 31), "S5"),
        ]
    }
    texts = {
        "S0": "株式会社アドウィルののれんを減損した",
        "S1": "減損の兆候はない",
        "S3": "株式会社アドウィルに係るのれんを減損した",
        "S5": "株式会社アドウィルの減損",
    }

    def read(doc_id: str) -> bytes:
        return _archive([("jpcrp_cor:NotesTextBlock", texts[doc_id])])

    items = candidate_items([_deal(), _deal(key="2-E1", acquiree_name="別の会社")], reports, read)

    assert items == [
        {
            "key": "1-E1",
            "acquirer": "買い手株式会社",
            "acquiree_name": "株式会社アドウィル",
            "base_year": 2021,
            "passages": [
                {"periodEnd": "2023-03-31", "text": "株式会社アドウィルに係るのれんを減損した"}
            ],
        }
    ]


def 確かめた結果を案件ごとに読む(tmp_path: Path) -> None:
    items = [{"key": "1-E1"}, {"key": "2-E1"}]
    split_candidates(items, tmp_path)
    results = [{"key": "1-E1", "impaired": True}, {"key": "2-E1", "impaired": False}]
    (tmp_path / "out" / "chunk_000.json").write_text(json.dumps(results), encoding="utf-8")

    assert read_impairments(tmp_path) == {"1-E1": True, "2-E1": False}


def 出力の並びが入力と違えば止める(tmp_path: Path) -> None:
    split_candidates([{"key": "1-E1"}, {"key": "2-E1"}], tmp_path)
    results = [{"key": "2-E1", "impaired": True}, {"key": "1-E1", "impaired": False}]
    (tmp_path / "out" / "chunk_000.json").write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(ValueError, match="chunk_000"):
        read_impairments(tmp_path)


def 三期後の有報が無い案件は空にし候補に無い案件は減損していないとみなす() -> None:
    reports = {
        "E1": [(date(2021, 3, 31), "S1"), (date(2024, 3, 31), "S4")],
        "E2": [(date(2021, 3, 31), "S9")],
    }
    deals = [_deal(), _deal(key="2-E1"), _deal(key="3-E2", edinetCode="E2")]

    labels = label_impairments(deals, reports, {"1-E1": True})

    assert labels == {"1-E1": True, "2-E1": False, "3-E2": None}
