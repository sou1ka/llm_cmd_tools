# disk_health

rag-server の定期ジョブ（`[[server.job]]`）。指定したマウントポイントの使用率と空きを見て、閾値を超えたものごとに話題 JSON を標準出力へ1行ずつ出す。

- LLM は使わない。文面はテンプレート
- SMART は未対応（将来追加予定）

## 動作環境

- Linux (Fedora)
- Python 3（標準ライブラリのみ）

## 設置

1. ディレクトリごと配置する（例: `/opt/agent/jobs/disk_health/`）
2. `.env.example` を `.env` にコピーして編集する

   ```sh
   cp .env.example .env
   ```

3. rag-server の `config.toml` の `[server.topics]` の `kinds` に `"system"` を追加する（ユーザー作業。下記参照）

同名の環境変数があれば `.env` より優先される。空文字の環境変数は未設定扱い。

## 設定項目

| 項目 | 必須 | 説明 |
| --- | --- | --- |
| `DISK_MOUNTS` | 必須 | 監視するマウントポイント（カンマ区切り） |
| `DISK_LABELS` | 任意 | 文面での呼び名。`/=システムディスク,/home=データ用ディスク` の形式。無ければマウントポイントをそのまま使う |
| `DISK_WARN_PERCENT` | 任意（既定 90） | 警告にする使用率（%） |
| `DISK_CRIT_PERCENT` | 任意（既定 95） | 危険にする使用率（%） |
| `DISK_WARN_FREE_GB` | 任意（既定 20） | 警告にする空き容量（GB） |
| `DISK_CRIT_FREE_GB` | 任意（既定 5） | 危険にする空き容量（GB） |

- GB は 1024^3 バイト
- `*_PERCENT` は 0〜100 の整数、`*_FREE_GB` は 0 以上
- WARN と CRIT の大小が逆（WARN の使用率が CRIT より大きい、または WARN の空きが CRIT より小さい）だとエラー

## 判定

| 段階 | 条件 | priority |
| --- | --- | --- |
| 危険（crit） | 使用率 >= `DISK_CRIT_PERCENT` または 空き < `DISK_CRIT_FREE_GB` | 3 |
| 警告（warn） | 使用率 >= `DISK_WARN_PERCENT` または 空き < `DISK_WARN_FREE_GB` | 1 |

- 使用率は `df` と同じ `used / (used + avail)` の切り上げ（root 予約分を含めない）
- 空きの表示は切り捨て。10GB 未満は小数1桁

## 出力

話題 JSON を1行ずつ標準出力に出す（ログは stderr）。

```json
{"kind":"system","ref":"disk:/home:warn:2026-11-13","body":"データ用ディスクの空きが残り45GB（使用率92%）。","priority":1,"expires_at":"2026-11-13T23:59:59+09:00"}
```

- `ref` に日付と段階が入るため、状態が続く間は1日1回話題になる。警告から危険に上がった日は、もう一度話題になる
- `expires_at` はその日の 23:59:59（ローカルタイムゾーン）

## 終了コード

| 状況 | stdout | 終了コード |
| --- | --- | --- |
| 該当なし | 空 | 0 |
| マウントされていないパスがある | 他のマウントの出力のみ | 1 |
| `DISK_MOUNTS` 未設定、閾値が不正 | 空 | 1 |

マウントされていないパスはスキップして stderr に理由を出し、他は続行して最後に exit 1 にする。出力済みの行は取り込まれる。

## rag-server への登録（config.toml）

```toml
[server.topics]
kinds = ["news", "system"]   # 既存の kinds に "system" を追加

[[server.job]]
name    = "disk_health"
cron    = "0 0 * * * *"      # 毎時0分（秒から始まる6フィールド）
cmd     = ["python3", "/path/to/disk_health/disk_health.py"]
timeout = 30.0
```

## 単体での確認

```sh
python3 disk_health.py; echo $?
```

閾値を一時的に下げて出力を確かめる。

```sh
DISK_WARN_PERCENT=0 python3 disk_health.py
```

出力が JSON として正しいか検証する。

```sh
python3 disk_health.py 2>/dev/null | python3 -c 'import sys,json; [json.loads(l) for l in sys.stdin if l.strip()]'
```

## テスト

`disk_health` ディレクトリで実行する。

```sh
python3 -m unittest discover -s tests
```
