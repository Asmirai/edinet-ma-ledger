import json
from pathlib import Path

import pytest

from ledger.chunks import read_outputs, write_chunks


def 決まった件数ずつ入力のファイルに分ける(tmp_path: Path) -> None:
    write_chunks([{"key": str(i)} for i in range(5)], tmp_path, 2)

    names = sorted(p.name for p in (tmp_path / "in").glob("*.jsonl"))
    assert names == ["chunk_000.jsonl", "chunk_001.jsonl", "chunk_002.jsonl"]
    assert (tmp_path / "out").is_dir()


def 前に分けた入力がある所には分けない(tmp_path: Path) -> None:
    write_chunks([{"key": "1"}], tmp_path, 2)

    with pytest.raises(FileExistsError):
        write_chunks([{"key": "1"}], tmp_path, 2)


def 出力を入力の順に読む(tmp_path: Path) -> None:
    write_chunks([{"key": "1"}, {"key": "2"}], tmp_path, 2)
    results = [{"key": "1", "v": True}, {"key": "2", "v": False}]
    (tmp_path / "out" / "chunk_000.json").write_text(json.dumps(results), encoding="utf-8")

    assert read_outputs(tmp_path) == results


def 出力の並びが入力と違えば止める(tmp_path: Path) -> None:
    write_chunks([{"key": "1"}, {"key": "2"}], tmp_path, 2)
    results = [{"key": "2"}, {"key": "1"}]
    (tmp_path / "out" / "chunk_000.json").write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(ValueError, match="chunk_000"):
        read_outputs(tmp_path)
