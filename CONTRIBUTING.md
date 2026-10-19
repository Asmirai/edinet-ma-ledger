# 参加のしかた

台帳の誤りの報告、バグの報告、コードの PR を受け付けています。台帳の値が原文と違うときは、PR ではなく issue で知らせてください。台帳の CSV はリポジトリに入れず、Release に添付しているためです。

## 台帳の誤りを報告する

[issue の「台帳の誤り」](https://github.com/Asmirai/edinet-ma-ledger/issues/new?template=data_error.yml)から、次の4つを書いてください。

| 項目 | 書くこと |
|---|---|
| deal_id | 誤りのある行の `deal_id` |
| 列 | 誤りのある列の名前（[docs/ma_ledger_csv.md](docs/ma_ledger_csv.md) の名前） |
| 正しい値 | 原文に書かれている値。金額なら原文の単位も |
| 出典の docID | 正しい値が書かれた有価証券報告書の書類番号。CSV の `source_doc_ids` の列にあります |

台帳の名前と金額は、AI（Claude）が注記から抜き出した値です。156件を原文と見比べた抜き取りでは、取得原価の誤りは2件（1.3%）でした。取得原価の単位の読み違い（円と千円の取り違え）は、一部の行に残っていることが分かっています。報告を受けたら原文で確かめ、誤りであれば台帳を直します。

## 開発の環境を作る

[uv](https://docs.astral.sh/uv/) を入れ、次を実行します。Python 3.12 は uv が用意します。

```bash
git clone https://github.com/Asmirai/edinet-ma-ledger.git
cd edinet-ma-ledger
uv sync
```

PR を出す前に、CI と同じチェックを手元で通してください。

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright          # strict
uv run pytest -q --cov       # カバレッジが80%を下回ると落ちる
node --test site/model.test.mjs
```

テストは `data/` を使いません。EDINET の API キーと書類が無くても通ります。API キーが要るのは、台帳を EDINET の書類から作り直すときだけです（手順は README）。

## テストを書く

テストは、t-wada の TDD（Red → Green → Refactor）の順に書きます。失敗するテストを先に書き、実装の前に落ちることを確かめてから、通る最小の実装を書きます。

- **置き場所**：対象と同じフォルダの `<モジュール名>.test.py`（例：`ledger/scorer.py` のテストは `ledger/scorer.test.py`）。横断の `tests/` フォルダは作りません
- **関数名**：日本語で、何を確かめるかを書きます（例：`上乗せ率が上限を超える候補は受け付けない`）。`test_` は付けません
- **集め方**：[conftest.py](conftest.py) が `*.test.py` で定義した関数をすべてテストとして集めます。`_` で始まる関数と、ほかのモジュールから import した関数は集めません。ヘルパーには `_` を付けてください
- **パラメータ化**：この集め方では `pytest.mark.parametrize` が効きません。いくつかの入力を確かめるときは、テストの中でループを回します
- **モック**：外部 API・ファイル・時刻のような、境界の外側だけを差し替えます。業務のロジックはモックしません

コードの型は basedpyright の strict で確かめます。型の情報が無いライブラリ（scikit-learn）は、`ledger/scorer.py` のように境目で `Any` として扱います。

## ブランチと PR

1. 変えたいことを issue に書きます。小さな直しなら省いて構いません
2. ブランチを `feature/<issue番号>/<短い名前>` で切ります（例：`feature/38/project-docs`）。外部の方は fork して、同じ名前のブランチを切ってください
3. コミットは、テストと実装を分けます。テストのコミットを先にします
4. Draft PR を出します。本文は [PR のテンプレート](.github/PULL_REQUEST_TEMPLATE.md)に沿って、1行目に `Closes #<issue番号>` を書きます
5. CI（ruff・basedpyright・pytest・Node のテスト）が通り、レビューで承認されたらマージします

`main` へ直接 push しません。

## 秘密情報

EDINET の API キーは `.env` に書き、コミットしません。詳しくは [SECURITY.md](SECURITY.md) にあります。

## ライセンス

送ってもらったコードは、このリポジトリと同じ MIT ライセンス（[LICENSE](LICENSE)）で公開します。
