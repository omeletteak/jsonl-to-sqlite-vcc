# trace-vcc-sqlite

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

### 4. Export — フィルタ結果を JSONL に戻す

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
    source_file TEXT    NOT NULL,
    ingested_at TEXT    NOT NULL
);
```

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
  ingest.py        # JSONL → SQLite
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
