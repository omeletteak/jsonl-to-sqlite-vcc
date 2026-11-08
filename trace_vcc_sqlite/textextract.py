"""Extract searchable plain text from a Claude Code JSONL record."""

from __future__ import annotations

# Record types whose text is worth indexing. Others (queue-operation,
# file-history-snapshot, ...) are bookkeeping or duplicate message text.
_INDEXED_TYPES = {"user", "assistant", "summary", "system"}


def _values(obj) -> list[str]:
    """Flatten the scalar values of a tool input (keys are left out)."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [v for x in obj.values() for v in _values(x)]
    if isinstance(obj, list):
        return [v for x in obj for v in _values(x)]
    if obj is None or isinstance(obj, bool):
        return []
    return [str(obj)]


def _block_text(block) -> list[str]:
    if isinstance(block, str):
        return [block]
    if not isinstance(block, dict):
        return []
    btype = block.get("type")
    if btype == "text":
        return [block.get("text") or ""]
    if btype == "thinking":
        return [block.get("thinking") or ""]
    if btype == "tool_use":
        parts = [block.get("name") or ""]
        parts.extend(_values(block.get("input")))
        return parts
    if btype == "tool_result":
        inner = block.get("content")
        if isinstance(inner, list):
            out = []
            for b in inner:
                out.extend(_block_text(b))
            return out
        if isinstance(inner, str):
            return [inner]
    return []


def extract_text(record: dict) -> str | None:
    """Return the searchable text of a record, or None if nothing to index."""
    if record.get("type") not in _INDEXED_TYPES:
        return None

    parts: list[str] = []
    if isinstance(record.get("summary"), str):
        parts.append(record["summary"])

    msg = record.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                parts.extend(_block_text(block))
    elif isinstance(record.get("content"), str):
        parts.append(record["content"])

    text = "\n".join(p for p in parts if p).strip()
    return text or None
