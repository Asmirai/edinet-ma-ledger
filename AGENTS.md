# AGENTS.md

このリポジトリで作業する人と AI エージェント向けの決まりです。入口は [README.md](README.md)、参加のしかたは [CONTRIBUTING.md](CONTRIBUTING.md) にあります。

## このリポジトリがすること

上場企業の有価証券報告書（EDINET）の「企業結合等関係」の注記から買収の台帳を作り、買収から3年以内にのれんを減損するかを予測するモデルを公開します。記事（Qiita）・公開 CSV（Release）・ブラウザのシミュレーター（GitHub Pages）・ノートブックの4つから人が来ます。どれも同じ数字を出すことを、テストと出力の比較で保ちます。

## データの流れと保存するファイル

`data/` は Git に入れません（EDINET の書類と途中の生成物）。公開するのは `data/public/ma_ledger.csv` だけで、Release に置きます。

| 段 | コマンド | 読むもの | 書くもの |
|---|---|---|---|
| 書類一覧 | `ledger.document_lists` | EDINET API | `data/document_lists/YYYY-MM-DD.json` |
| 注記 | `ledger.notes` | 書類一覧、EDINET API | `data/documents/{docID}.zip`、`data/notes/{docID}.json` |
| 抜き出し | `ledger.claude_deals split`・`check` | 注記 | `data/claude_deals/y{年}/in`・`out`（out はサブエージェントが書く） |
| 台帳 | `ledger.acquisitions` | 抜き出しの結果 | `data/acquisitions.csv` |
| 誤り率 | `ledger.review sample`・`score` | 抜き出しの結果、注記 | `data/review/` |
| ROA | `ledger.roa` | 台帳、有報の ZIP | `data/financials.json`（読んだ値の控え）、`data/acquisitions_roa.csv` |
| 業種 | `ledger.industries split` | ROA 付きの台帳 | `data/industries/`（out はサブエージェント） |
| 減損 | `ledger.impairments split`・`label` | ROA 付きの台帳、有報の ZIP | `data/impairments/`、`data/impairments.csv` |
| 目的と業種の関係 | `ledger.purposes split` | 抜き出しの入力、ROA 付きの台帳 | `data/purposes/`（out はサブエージェント） |
| 買い手の財務 | `ledger.dataset`（`factors`・`export`・`figures` から呼ぶ） | 有報の ZIP | `data/profiles.json`（読んだ値の控え） |
| 要因の集計 | `ledger.factors` | 上のすべて | `data/factors.md` |
| 公開 CSV | `ledger.export` | 上のすべて | `data/public/ma_ledger.csv` |
| 予測モデル | `ledger.scorer` | 公開 CSV だけ | `data/scorer.md`、`--export-model` で `site/model.json` |
| ROA の予測（付録） | `ledger.predict` | ROA 付きの台帳、業種 | `data/predict.md` |
| 図 | `ledger.figures <出力するフォルダ>` | 上のすべてと公開 CSV | 記事の図6枚 |

生データ（EDINET から取った書類）は書き換えません。加工した結果は別のファイルに書きます。読んだ値の控え（`financials.json`・`profiles.json`）は、まだ読んでいない有報だけを読み足すために使います。

## 層の分け方

- **公開台帳の決まり**（`public`）：置き場所・列・値の種類（`Literal`）・成否を付けられる最後の年・のれんの割合の上限。どの層も import してよく、`public` はほかを import しない
- **予測モデル**（`scorer`）と**ブラウザ**（`site/model.js`）は、`public` だけに依存する。データ取得の層（`edinet`・`notes` から `dataset` まで）を import しない。Colab やブラウザで、公開 CSV だけで動かすため
- **エントリポイント**（各モジュールの `main`）は、引数の読み取り（argparse）とファイルの読み書きだけにする。集計やレポートの組み立ては、テストできる関数に出す

## 変えるときの決まり

- 構造の変更（振る舞いを変えない）と、振る舞いの変更は、別の PR にする。構造を変える前に、いまの出力（`data/*.md`・公開 CSV・候補の確率）を控え、変えた後に1バイトずつ比べる
- 同じ知識が2か所に出たら、2回目でまとめる。形が似ているだけなら、3回目まで待つ
- 有限の値は `str` でなく `Literal` で表す。単純なデータの形（行の dict）には、専用の型を作らない
- `*.test.py` では `pytest.mark.parametrize` が効かない。場合ごとにテストを分けるか、ループの `assert` に場合の値を添える。`pytest.raises` はループの中に入れない
- モデルの式を変えたら、`uv run python -m ledger.scorer --export-model site/model.json` で書き出し直し、`node --test site/model.test.mjs` で Python と同じ確率になることを確かめる
- 記事の数字（失敗率・AUC・候補の確率）を変える変更は、README・モデルカード・ノートブック・シミュレーターのどれに数字が出ているかを洗い出し、すべて直す

## 確かめること（CI と同じ）

```bash
uv run ruff check . && uv run ruff format --check .
uv run basedpyright
uv run pytest -q --cov
node --test site/model.test.mjs
```

カバレッジが80%を下回ると CI が落ちます。ノートブックは strict の型チェックから外しています（ruff の検査は対象）。

## 文章の決まり

README・docs・PR・issue・コミットの日本語は、結論から一文一義で書きます。句読点は「、」「。」です。

- 比喩を使わない（距離で言い表す「近い・遠い事業」も比喩）。業種の関係は「同業種・関連業種・異業種」
- 決まった言い方のない言葉を作らない。専門用語は正式な名前を使い、初めて出る所で短く説明する
- 確かめていないことは「未検証」と書く。列挙を「など」で濁さない
- 会社名は「合同会社Asmirai」（「株式会社」としない）
- データを使う文書には、出典（EDINET、公共データ利用規約 第1.0版）と、合同会社Asmirai が加工したことを書く

## ブランチと PR

- ブランチは `feature/<issue番号>/<短い名前>`。`main` へ直接 push しない
- テストと実装は別のコミットにする
- PR の本文には、確かめたコミットと、流したチェックの名前と件数を書く
- CI が通ったことを確かめてからマージする
