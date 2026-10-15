"""*.test.py の集め方（AGENTS.md のテストの決めごと：対象と同じ場所に置き、関数名は日本語）。

pytest はファイル名の点をパッケージの区切りと取り違える。そこで *.test.py だけを、
点を下線に替えた名前（ledger.edinet_test）で読み込む。
関数名に test_ を付けないので、*.test.py に限り、そのファイルで定義した関数をすべて集める。
"""

import importlib.util
import inspect
import sys
from importlib.abc import Loader
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent


class _TestFile(pytest.Module):
    def _getobj(self) -> ModuleType:
        parts = self.path.resolve().relative_to(ROOT).parts
        name = ".".join([*parts[:-1], parts[-1].removesuffix(".py").replace(".", "_")])
        # pytest の assert の書き換えを通す。通さないと、失敗しても値の差が出ない
        rewriter = next(
            (
                finder
                for finder in sys.meta_path
                if isinstance(finder, Loader) and type(finder).__name__ == "AssertionRewritingHook"
            ),
            None,
        )
        spec = importlib.util.spec_from_file_location(name, self.path, loader=rewriter)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module


def pytest_pycollect_makemodule(
    module_path: Path, parent: pytest.Collector
) -> pytest.Module | None:
    if not module_path.name.endswith(".test.py"):
        return None
    # pytest の from_parent は型を書き切っていない
    return _TestFile.from_parent(parent, path=module_path)  # pyright: ignore[reportUnknownMemberType]


def pytest_pycollect_makeitem(
    collector: pytest.Collector, name: str, obj: object
) -> pytest.Function | None:
    if not isinstance(collector, _TestFile) or name.startswith("_") or not inspect.isfunction(obj):
        return None
    # import した関数はテストではない
    if obj.__module__ != collector.obj.__name__:
        return None
    return pytest.Function.from_parent(collector, name=name)  # pyright: ignore[reportUnknownMemberType]
