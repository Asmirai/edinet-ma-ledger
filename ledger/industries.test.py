import json
from pathlib import Path

import pytest

from ledger.industries import read_industries, split_businesses


def _row(deal_id: int, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "deal_id": deal_id,
        "edinetCode": "E1",
        "filerName": "買った株式会社",
        "acquiree_name": f"買われた{deal_id}",
        "business": "ソフトウェアの開発",
        "primary": True,
        "success": True,
    }
    return row | changes


def _inputs(out_dir: Path) -> list[list[dict[str, str]]]:
    return [
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for path in sorted((out_dir / "in").glob("chunk_*.jsonl"))
    ]


def 成否を付けた主の行だけを150件ずつに分ける(tmp_path: Path) -> None:
    rows = [_row(i) for i in range(151)]
    rows += [_row(900, primary=False), _row(901, success=None)]

    split_businesses(rows, tmp_path)

    chunks = _inputs(tmp_path)
    assert [len(chunk) for chunk in chunks] == [150, 1]
    assert chunks[0][0] == {
        "key": "0-E1",
        "acquirer": "買った株式会社",
        "acquiree_name": "買われた0",
        "business": "ソフトウェアの開発",
    }
    assert (tmp_path / "out").is_dir()


def 前に分けた入力がある所には分けない(tmp_path: Path) -> None:
    split_businesses([_row(1)], tmp_path)

    with pytest.raises(FileExistsError):
        split_businesses([_row(1)], tmp_path)


def _write_output(out_dir: Path, results: list[dict[str, object]]) -> None:
    (out_dir / "out" / "chunk_000.json").write_text(
        json.dumps(results, ensure_ascii=False), encoding="utf-8"
    )


def 出力の業種を案件ごとに読む(tmp_path: Path) -> None:
    split_businesses([_row(1), _row(2)], tmp_path)
    _write_output(
        tmp_path, [{"key": "1-E1", "industry": "情報・通信業"}, {"key": "2-E1", "industry": None}]
    )

    assert read_industries(tmp_path) == {"1-E1": "情報・通信業", "2-E1": None}


def 業種の一覧に無い業種が出力にあれば止める(tmp_path: Path) -> None:
    split_businesses([_row(1)], tmp_path)
    _write_output(tmp_path, [{"key": "1-E1", "industry": "IT"}])

    with pytest.raises(ValueError, match="IT"):
        read_industries(tmp_path)


def 出力の並びが入力と違えば止める(tmp_path: Path) -> None:
    split_businesses([_row(1), _row(2)], tmp_path)
    _write_output(tmp_path, [{"key": "2-E1", "industry": None}, {"key": "1-E1", "industry": None}])

    with pytest.raises(ValueError, match="chunk_000"):
        read_industries(tmp_path)
