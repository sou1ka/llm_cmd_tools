# cert_expiry

rag-server の定期ジョブ（`[[server.job]]`）。指定したホストの TLS 証明書（HTTPS 等）の期限を見て、期限が近い・検証に失敗したものごとに話題 JSON を標準出力へ1行ずつ出す。

- 見るのは TLS 証明書の期限。ドメインの登録期限ではない（それは `domain_expiry`）
- LLM は使わない。文面はテンプレート

## 動作環境

- Linux (Fedora)
- Python 3（標準ライブラリのみ）
- 監視対象のホストにネットワークで接続できること

## 設置

1. ディレクトリごと配置する（例: `/opt/agent/jobs/cert_expiry/`）
2. `.env.example` を `.env` にコピーして編集する

   ```sh
   cp .env.example .env
   ```

3. rag-server の `config.toml` の `[server.topics]` の `kinds` に `"system"` が必要（`disk_health` と共通）

同名の環境変数があれば `.env` より優先される。空文字の環境変数は未設定扱い。

## 設定項目

| 項目 | 必須 | 説明 |
| --- | --- | --- |
| `CERT_HOSTS` | 必須 | 確認するホスト（カンマ区切り）。`host` または `host:port`。ポートを省くと 443 |
| `CERT_WARN_DAYS` | 任意（既定 28） | 警告にする残り日数 |
| `CERT_CRIT_DAYS` | 任意（既定 7） | 危険にする残り日数 |
| `CERT_TIMEOUT` | 任意（既定 10） | 接続のタイムアウト（秒） |

- `CERT_HOSTS` の空要素と重複は無視する。ポートは 1〜65535 の整数
- `*_DAYS` は 0 以上の整数。`CERT_WARN_DAYS` が `CERT_CRIT_DAYS` より小さいとエラー
- `CERT_TIMEOUT` は 0 より大きい数
- IPv6 アドレスの直書きには対応していない

```
CERT_HOSTS=example.com,mail.example.com:993
```

## 判定

検証ありで TLS 接続し、証明書の `notAfter` を読む。

| 段階 | 条件 | priority |
| --- | --- | --- |
| 危険（crit） | 残り日数 <= `CERT_CRIT_DAYS`、期限切れ、または証明書の検証に失敗 | 3 |
| 警告（warn） | 残り日数 <= `CERT_WARN_DAYS` | 1 |

- 残り日数は `notAfter` と現在時刻の差（切り捨て）。期限を過ぎていれば負
- 期限切れ・ホスト名の不一致・信頼できない発行元などの検証の失敗は、すべて危険（crit）の話題になる
- 接続の失敗（名前解決・接続拒否・タイムアウト等）は話題にしない。stderr に理由を出し、全ホストを確認した後に exit 1

## 出力

話題 JSON を1行ずつ標準出力に出す（ログは stderr）。`kind` は `"system"`。

| 状況 | body 例 |
| --- | --- |
| 残り1日以上 | example.comの証明書の期限まであと20日（11月24日）。 |
| 期限が今日 | example.comの証明書の期限は今日（11月24日）。 |
| 期限切れ | example.comの証明書が期限切れ。 |
| 検証の失敗 | example.comの証明書の検証に失敗（Hostname mismatch, certificate is not valid for 'example.com'.）。 |

```json
{"kind":"system","ref":"cert:example.com:warn:2026-11-04","body":"example.comの証明書の期限まであと20日（11月24日）。","priority":1,"expires_at":"2026-11-04T23:59:59+09:00"}
```

- `ref` は `cert:{name}:{warn|crit}:{日付}`。`name` はポートが 443 ならホスト名、それ以外は `host:port`
- 状態が続く間は1日1回話題になる。警告から危険に上がった日は、もう一度話題になる
- `expires_at` はその日の 23:59:59（ローカルタイムゾーン）

## 終了コード

| 状況 | stdout | 終了コード |
| --- | --- | --- |
| 該当なし | 空 | 0 |
| 期限が近い・検証の失敗がある | 話題の行 | 0 |
| 接続に失敗したホストがある | 他のホストの出力のみ | 1 |
| `CERT_HOSTS` 未設定、設定値が不正 | 空 | 1 |

接続に失敗したホストはスキップして stderr に理由を出し、他は続行して最後に exit 1 にする。出力済みの行は取り込まれる。

## 注意

- レンタルサーバーで独自ドメインに証明書を設定していないと、サーバー会社の共用証明書（`*.<サーバー会社のドメイン>` 用のもの）が返る。その場合は「検証に失敗（Hostname mismatch…）」の話題が毎日出る。証明書を設定していないホストは `CERT_HOSTS` から外す
- `ref` は日付単位なので、同じ日に手動実行した後の cron 実行の話題は重複として弾かれる

## rag-server への登録（config.toml）

`/path/to` は環境に合わせて置き換える。

```toml
[[server.job]]
name    = "cert_expiry"
cron    = "0 0 9 * * *"          # 毎朝9時（秒から始まる6フィールド）
cmd     = ["python3", "/path/to/cert_expiry/cert_expiry.py"]
timeout = 120.0
```

## 単体での確認

```sh
python3 cert_expiry.py; echo $?
```

標準エラーに `ホスト: expires ... days=N level=...` が出る。

警告の閾値を一時的に大きくして出力を確かめる。

```sh
CERT_WARN_DAYS=9999 python3 cert_expiry.py
```

出力が JSON として正しいか検証する。

```sh
python3 cert_expiry.py 2>/dev/null | python3 -c 'import sys,json; [json.loads(l) for l in sys.stdin if l.strip()]'
```

## テスト

`cert_expiry` ディレクトリで実行する。

```sh
python -m unittest discover -s tests
```

## ファイル構成

| ファイル | 説明 |
| --- | --- |
| `cert_expiry.py` | ジョブ本体 |
| `.env.example` | 設定の例。`.env` にコピーして使う |
| `tests/` | 単体テスト（`helpers.py`、`test_cert_expiry.py`、`test_main_cli.py`） |
