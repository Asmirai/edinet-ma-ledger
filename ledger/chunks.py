"""サブエージェントに渡す入力を決まった件数ずつのファイルに分け、出力を読み戻す。

業種分け・のれんの減損・買収の目的の読み取りで使う。入力と出力の並びを key で突き合わせるので、
分け方と読み方を1か所にそろえないと、片方だけ直したときに案件と結果が食い違う。
"""

import json
from pathlib import Path
from typing import Any, cast


def write_chunks(items: list[dict[str, Any]], out_dir: Path, size: int) -> None:
    """items を size 件ずつ out_dir/in/chunk_NNN.jsonl に書く。前に分けた入力があれば止める。"""
    if any((out_dir / "in").glob("chunk_*.jsonl")):
        raise FileExistsError(f"{out_dir / 'in'} に前に分けた入力がある")
    (out_dir / "in").mkdir(parents=True, exist_ok=True)
    (out_dir / "out").mkdir(exist_ok=True)
    for start in range(0, len(items), size):
        lines = "".join(
            json.dumps(item, ensure_ascii=False) + "\n" for item in items[start : start + size]
        )
        (out_dir / "in" / f"chunk_{start // size:03d}.jsonl").write_text(lines, encoding="utf-8")


def read_outputs(out_dir: Path) -> list[dict[str, Any]]:
    """out_dir/out の出力を入力の順に読む。出力の key の並びが入力と違えば止める。"""
    outputs: list[dict[str, Any]] = []
    for in_path in sorted((out_dir / "in").glob("chunk_*.jsonl")):
        out_path = out_dir / "out" / f"{in_path.stem}.json"
        keys = [
            cast(dict[str, Any], json.loads(line))["key"]
            for line in in_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        results = cast(list[dict[str, Any]], json.loads(out_path.read_text(encoding="utf-8")))
        if [result["key"] for result in results] != keys:
            raise ValueError(f"{out_path.name} の並びが入力と違う")
        outputs.extend(results)
    return outputs
