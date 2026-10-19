# セキュリティ

## 脆弱性を報告する

脆弱性を見つけたら、公開の issue や PR に書かず、GitHub の private vulnerability reporting で報告してください。リポジトリの Security タブの「Report a vulnerability」から、または次の URL から送れます。

https://github.com/Asmirai/edinet-ma-ledger/security/advisories/new

報告は、合同会社Asmirai のうちリポジトリを管理する者だけが読めます。直し方と公開の時期は、その画面のやり取りで相談します。

対象は `main` の最新のコードと、GitHub Actions の設定（`.github/`）です。

## 秘密情報の扱い

このリポジトリで秘密情報に当たるのは、EDINET API v2 のキー（`EDINET_API_KEY`）だけです。台帳を EDINET の書類から作り直すときに使います。README の「すぐ試す」と、テストと CI では使いません。

- キーは `.env` に書きます。`cp .env.example .env` で作り、`uv run --env-file .env ...` で読み込みます
- `.env` は `.gitignore` に入れてあります。コミットしません
- `.env.example` には、キーの名前と説明だけを書きます。値は書きません
- キーを issue・PR・ログ・コードに貼りません
- キーを誤って公開したら、EDINET の API キーの発行画面（https://api.edinet-fsa.go.jp/WEEE0090.aspx ）で新しいキーを取ってください。発行し直したときに古いキーが使えなくなるかは未検証です

CI は秘密情報を使いません。GitHub のサーバー（`ubuntu-latest`）で動かし、権限はリポジトリの読み取り（`contents: read`）だけにしています。理由は [docs/decisions/34-github-hosted-ci.md](docs/decisions/34-github-hosted-ci.md) にあります。
