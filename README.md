# edinet-ma-ledger

**上場企業の有価証券報告書から「企業結合等関係」の注記を取り出し、手元の LLM で M&A の一覧（台帳）を作る。** その台帳を使って、どういう買い方がうまくいったかを確かめ、テックブログの記事にする。

データは EDINET API v2 から取る。EDINET の情報は公共データ利用規約（PDL1.0）で公開されている。台帳を公開するときは、出典と、加工したこと・加工した者を書く。

## 今の段階

2016-10〜2025-09 の有報から買収案件を抜き出した。次は、同じ案件の重複をまとめ、抜き出しの誤り率を確かめる（#7）。

## 準備

1. EDINET の閲覧サイトでアカウントを作り、API キーを取る
2. `cp .env.example .env` で `.env` を作り、`EDINET_API_KEY` にキーを書く。`.env` はコミットしない
3. `uv sync` で依存を入れる

## 書類一覧を取る

```bash
uv run --env-file .env python -m ledger.document_lists 2024-10-01 2025-09-30
```

期間の各日の書類一覧を `data/document_lists/YYYY-MM-DD.json` に保存し、月ごとの有報の件数を出す。呼ぶたびに1秒空ける。途中で止まっても、保存済みの日は取り直さない。当日の一覧はまだ増えるので、終わりの日は昨日以前にする。

## 企業結合の注記を取り出す

```bash
uv run --env-file .env python -m ledger.notes 2024-10-01 2025-09-30
```

先に取った書類一覧から、期間に提出された会社の有報を選ぶ（投資信託などのファンドの有報は除く）。1件ずつ CSV の ZIP を取って `data/documents/{docID}.zip` に保存し、「企業結合等関係」の注記の文章を `data/notes/{docID}.json` に保存する。注記が無い有報も、空の一覧で保存する。保存済みの有報は取り直さない。

## 注記から買収案件を抜き出す

台帳は、Claude Code のサブエージェントに注記を読ませて作った。手元の LLM（qwen3:8b）での抜き出しも残してあるが、こちらは比べるためのもので、台帳には使っていない。

### Claude Code のサブエージェントで抜き出す

```bash
uv run python -m ledger.claude_deals split 2023-10-01 2024-09-30 data/claude_deals/y2024
```

期間内に出た有報の、中身のある注記を30件ずつ `in/chunk_NNN.jsonl` に分ける。前に分けた入力がある所には分けない。各ファイルを1つのサブエージェントに渡し（2024-09 までは Sonnet、2024-10 からは Opus）、`ledger/claude_deals_instructions.md` の指示で `out/chunk_NNN.json` に案件を書かせる。

```bash
uv run python -m ledger.claude_deals check data/claude_deals/y2024
```

出力を入力の原文と照らし合わせ、種類ごとの案件数と、原文に載っていない項目がある取得を出す。入力が無いとき、出力の無い入力があるとき、注記の並びが入力と違う出力があるときは止まる。

### 重複をまとめて台帳にする

```bash
uv run python -m ledger.acquisitions
```

`data/claude_deals/` の取得の案件から、同じ案件の重複をまとめて `data/acquisitions.csv` に書く。金額は円に直す。

- 同じ会社の後の有報に載った同じ案件（同じ名前で、企業結合日が1年以内）は1行にする。値は後の有報のものを使い、空の項目だけ前の有報で埋める
- 親会社と子会社が同じ日に同じ会社を買っていれば、行は会社ごとに残し、同じ `deal_id` を付ける
- 名前が無いもの、「当社」（逆取得）、「その他の企業結合」は除く
- 期首に取得していたと仮定した売上高は、担当によって差額と全体の額に割れたので入れない

### 誤り率を出す

```bash
uv run python -m ledger.review sample data/review
uv run python -m ledger.review score data/review
```

`sample` は年ごとに20件の注記を無作為に選び、10件ずつ `in/chunk_NNN.jsonl` に分ける。取得の案件が無い注記も選ぶ（見落としも数えるため）。各ファイルを Opus のサブエージェントに渡し、`ledger/review_instructions.md` の指示で原文と見比べさせる。`score` は、見落とし、取得でないのに取得とした数、項目ごとの誤りを数える。

### 買収前後の ROA で成否を付ける

```bash
uv run python -m ledger.roa
```

台帳の取得ごとに、買った会社の ROA（親会社株主に帰属する当期純利益 ÷ 期末の総資産）が買収の前後でどう変わったかを出し、`data/acquisitions_roa.csv` に書く。値は取得済みの有報の「主要な経営指標等の推移」（5期分）から読む。業種は EDINET コードリスト（`data/codelist/EdinetcodeDlInfo.csv`）から引く。

- 企業結合日を含む事業年度を基準の期とし、買収後3期の ROA の平均から買収前の期の ROA を引く（ΔROA）
- 同じ業種・同じ基準の年の会社の ΔROA の中央値を引き、0 より大きければ成功とする
- 決算期を変えた期、会計基準や連結・単体が変わった期をまたぐものは、理由を付けて空にする
- 読んだ値は `data/financials.json` に残し、次からはまだ読んでいない有報だけを読む

### 仮説を確かめる

```bash
uv run python -m ledger.industries split data/industries
uv run python -m ledger.hypotheses
```

`industries split` は、成否を付けた主の行の「事業の内容」を150件ずつに分ける。各ファイルを Sonnet のサブエージェントに渡し、`ledger/industry_instructions.md` の指示で買われた会社を33業種に分けさせる。`hypotheses` は、同業・のれんの割合・買う会社の業績・買収前の収益性でそろえた比較の4つの仮説を確かめ、`data/hypotheses.md` に書く。

### 成否を当てられるかを確かめる

```bash
uv run python -m ledger.predict
```

買収の時点で分かる情報（同業か、のれんの割合、買収の大きさ、買収前の ROA、業種、法的形式、議決権比率、それまでの買収の件数）で、買収前の ROA でそろえた成否を当てる。基準の年が2019年までで学習し、2020年からで確かめる。全件の成功率・ロジスティック回帰・勾配ブースティングの AUC などを `data/predict.md` に書く。

### 手元の LLM で抜き出す

```bash
uv run python -m ledger.deals
```

`data/notes/` の注記から、手元の LLM（Ollama の qwen3:8b）で買収案件を抜き出し、`data/deals/{docID}.json` に保存する。Ollama を先に起動しておく。モデルと接続先は、環境変数 `OLLAMA_MODEL`・`OLLAMA_URL` で変えられる。

抜き出しは二段に分ける。一段目で案件の一覧（名前・種類・期）を出させ、二段目で「取得」の案件ごとに、その案件の部分だけを渡して金額などを聞く。一度に全部を聞くと、長い注記で金額を取りこぼした。

数値はモデルに作らせない。名称と金額は原文に載っているかを機械で照らし合わせ、載っていない項目を案件の `unverified` に記録する。前の期の記載が無い注記の「前期」の案件と、同じ名前・同じ期の2件目は、モデルの作り話として除く。
