# Upstream: VCC (View-oriented Conversation Compiler)

このディレクトリ配下の skill は、以下のリポジトリから無改変で同梱したものです。

- リポジトリ: https://github.com/lllyasviel/VCC
- 同梱元コミット: [`b962af2`](https://github.com/lllyasviel/VCC/tree/b962af2d6644882486d74ee8bd4e18a299602445) (2026-05-02)
- ライセンス: Apache License 2.0 (全文は [`LICENSE`](LICENSE))
- 論文: "View-oriented Conversation Compiler for Agent Trace Analysis" — https://arxiv.org/abs/2603.29678

| このリポジトリ | 本家のパス |
|---|---|
| `conversation-compiler/` (`scripts/VCC.py` を含む) | `skills/conversation-compiler/` |
| `readchat/` | `skills/readchat/` |
| `searchchat/` | `skills/searchchat/` |
| `recall/` | `skills/recall/` |

## 本家に追従するには

```bash
git clone https://github.com/lllyasviel/VCC /tmp/VCC
cp -r /tmp/VCC/skills/. .claude/skills/
cp /tmp/VCC/LICENSE .claude/skills/LICENSE
```

更新したら上の「同梱元コミット」を書き換えてください。
