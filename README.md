# jsonl-to-sqlite-vcc

Claude Code の JSONL ログを SQLite に取り込み、VCC (View-oriented Conversation Compiler) と連携して Full/UI/Adaptive View を高速生成する軽量ツール。

## インストール

```bash
pip install -e ".[dev]"
```

## 使い方

### 1. Ingest — JSONL を SQLite に取り込む

```bash
# 単一ファイル
trace-vcc ingest traces.db conversation.jsonl

# ディレクトリ内の全 .jsonl を再帰的に取り込み
trace-vcc ingest traces.db ~/.claude/projects/my-project/
```

### 2. Query — データベースの中身を確認

```bash
# サマリー表示
trace-vcc query traces.db

# 任意の SQL (SELECT のみ)
trace-vcc query traces.db --sql "SELECT type, count(*) FROM traces GROUP BY type"

# ツール使用ランキング
trace-vcc query traces.db --sql \
  "SELECT tool_name, count(*) as n FROM traces WHERE tool_name IS NOT NULL GROUP BY tool_name ORDER BY n DESC"
```

### 3. View — フィルタして VCC でビュー生成

```bash
# 全レコードから Full View 生成
trace-vcc view traces.db

# セッション指定
trace-vcc view traces.db -w "session_id = ?" -p "abc-123-def"

# assistant + user のみ、grep 付き
trace-vcc view traces.db -w "type IN ('user','assistant')" --grep "error"

# 出力先指定
trace-vcc view traces.db -o ./output/
```

### 4. Search — 過去の会話を全文検索

```bash
# スペース区切りの語は AND。大文字小文字は区別しない
trace-vcc search traces.db "migration error"

# 日本語もそのまま（部分一致なので分かち書き不要）
trace-vcc search traces.db 認証 --type user --since 2026-11-01

# セッション単位でまとめる → そのまま VCC ビューを生成
trace-vcc search traces.db "sqlite" --sessions --view -o ./output/

# FTS5 の構文 (OR / NOT / NEAR / prefix*) を使う
trace-vcc search traces.db "pytest OR ruff" --fts --sort time
```

| オプション | 説明 |
|---|---|
| `-s, --session` | セッション ID（前方一致） |
| `--type` | レコード種別（複数指定可） |
| `--tool` | tool_name の部分一致 |
| `--since` / `--until` | タイムスタンプの範囲（`--until` は含まない） |
| `--sort rank\|time` | 関連度順（既定）か新しい順 |
| `-n, --limit` | 最大件数（既定 20） |
| `--sessions` | セッションごとにヒット数を集計 |
| `--view` | ヒットしたセッションの VCC ビューを生成 |

検索対象は user / assistant / summary / system レコードの本文、thinking、ツール呼び出しの引数、ツール結果です。SQLite FTS5 の trigram トークナイザで索引を作るので、3 文字以上の語は索引で引き、2 文字以下の語は LIKE で探します。索引は ingest 時に自動で更新され、既存の DB も初回の ingest / search 時に自動で移行されます。

### 5. Export — フィルタ結果を JSONL に戻す

```bash
# VCC に直接渡したい場合
trace-vcc export traces.db filtered.jsonl -w "tool_name = ?" -p "Read"
python3 .claude/skills/conversation-compiler/scripts/VCC.py filtered.jsonl
```

## スキーマ

```sql
CREATE TABLE traces (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    line_num    INTEGER NOT NULL,    -- 元の JSONL 行番号 (VCC 互換)
    type        TEXT,                -- user, assistant, system, result, ...
    subtype     TEXT,
    timestamp   TEXT,                -- ISO 8601
    uuid        TEXT,
    parent_uuid TEXT,
    tool_name   TEXT,                -- tool_use/tool_result から抽出
    model       TEXT,
    content     TEXT    NOT NULL,    -- 元の JSON をそのまま保存
    text        TEXT,                -- 検索用に抜き出した本文
    source_file TEXT    NOT NULL,
    ingested_at TEXT    NOT NULL
);
```

全文検索用に `traces_fts`（FTS5, trigram, `traces.text` を外部コンテンツとして参照）とトリガーを作ります。

インデックス: `session_id`, `type`, `timestamp`, `tool_name`, `(session_id, line_num)`, `(source_file, line_num)` (UNIQUE)

## ベンチマーク: SQLite vs 生JSONL

`bench/benchmark.py` で計測。各操作 20〜30 回の平均値。

### Part 1: 実データ（15 ファイル / 994 行 / 1.9 MB）

Raw(disk) は毎回ファイルから再読み込み+パース。Raw(mem) はメモリ上に展開済みの最善ケース。

| 操作 | Raw (disk) | Raw (mem) | SQLite | disk→sq | mem→sq |
|------|------------|-----------|--------|---------|--------|
| Count all | 13.06 ms | 0.00 ms | 0.30 ms | **43x** | 0.0x |
| Filter type='assistant' | 11.81 ms | 0.05 ms | 0.76 ms | **16x** | 0.1x |
| Filter tool='Read' | 8.80 ms | — | 0.28 ms | **31x** | — |
| GROUP BY type | 8.81 ms | 0.08 ms | 0.25 ms | **36x** | 0.3x |
| 4 queries in sequence | 8.95 ms | — | 2.25 ms | **4.0x** | — |

### Part 2: スケーリング（合成データ）

データが大きくなるほどSQLiteの優位が拡大する。

| Rows | 操作 | Raw (disk) | SQLite | Speedup |
|-----:|------|------------|--------|--------:|
| 1,000 | Filter type | 2.14 ms | 0.36 ms | **6x** |
| 1,000 | GROUP BY | 2.12 ms | 0.24 ms | **9x** |
| 5,000 | Filter type | 11.81 ms | 1.10 ms | **11x** |
| 5,000 | GROUP BY | 12.05 ms | 0.40 ms | **30x** |
| 10,000 | Filter type | 30.21 ms | 1.96 ms | **15x** |
| 10,000 | GROUP BY | 29.42 ms | 0.54 ms | **54x** |
| 50,000 | Filter type | 242.56 ms | 10.11 ms | **24x** |
| 50,000 | GROUP BY | 218.75 ms | 1.88 ms | **116x** |

### 結論

| シナリオ | 勝者 | 理由 |
|----------|------|------|
| 単発クエリ、データがメモリに載る | Raw JSONL | パース済みlistのフィルタはSQLiteのconnect+queryより速い |
| 毎回ディスクから読む | SQLite | ファイルI/O+json.loadsのコストが支配的 |
| 複数クエリを連続実行 | SQLite | ingest 1回で以後のクエリは全てインデックス利用 |
| 10K行以上 | SQLite | GROUP BY で100x超の差。スケーリングが線形 vs 定数的 |
| VCC連携（フィルタ→View生成） | SQLite | SQLで絞ってからVCCに渡すので、VCCの処理量も削減 |

## ファイル構成

```
trace_vcc_sqlite/
  __init__.py      # パッケージ
  schema.sql       # DDL
  fts.sql          # 全文検索インデックスとトリガー
  ingest.py        # JSONL → SQLite
  textextract.py   # レコードから検索用テキストを抽出
  search.py        # 全文検索
  adapter.py       # SQLite → VCC 入力
  cli.py           # CLI エントリポイント
```

## 依存

- Python >= 3.9
- click >= 8.0
- SQLite3 (Python 標準ライブラリ)
- VCC.py (conversation-compiler skill)

## License

MIT
