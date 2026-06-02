#!/usr/bin/env python3
"""Example usage-footer renderer for the `usage-footer` OpenClaw plugin.

Reads the openclaw.usageLine.v1 JSON contract on stdin, prints one footer line
on stdout. Exit non-zero (or print nothing) to fall back to no footer.

Wire it up in openclaw.json:

  "plugins": { "entries": { "usage-footer": { "enabled": true, "config": {
    "surfaces": { "discord": { "command": "/abs/path/to/discord.py" } }
  } } } }
"""
import json
import sys


def bar(pct, width=5):
    if pct is None:
        return "[" + "·" * width + "]"
    filled = max(0, min(width, round(pct / 100 * width)))
    return "[" + "⣿" * filled + "·" * (width - filled) + "]"


def k(n):
    if n is None:
        return "?"
    return f"{round(n / 1000)}k" if n >= 1000 else str(n)


def dur(seconds):
    """Compact reset time: 4h07m, 5.2d, 33m."""
    if seconds is None:
        return "?"
    s = max(0, int(seconds))
    if s >= 86400:
        return f"{s / 86400:.1f}d"
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 60}m"


def main():
    try:
        ctx = json.load(sys.stdin)
    except Exception:
        return 1

    model = ctx.get("model") or {}
    state = ctx.get("state") or {}
    usage = ctx.get("usage") or {}
    context = ctx.get("context") or {}

    name = model.get("display_name") or model.get("id") or "model"
    bits = [f"🤖 {name}"]

    if model.get("is_fallback"):
        bits.append("⤵")
    reasoning = model.get("reasoning")
    if reasoning:
        bits.append(str(reasoning))
    if state.get("fast_mode"):
        bits.append("⚡")

    head = " ".join(bits)

    pct = context.get("pct_used")
    max_tokens = context.get("max_tokens")
    ctx_part = f"📚 {bar(pct)}{k(max_tokens)}"

    io_part = f"↕ {k(usage.get('input_tokens'))}/{k(usage.get('output_tokens'))}"

    tail = [ctx_part, io_part]
    hit = usage.get("cache_hit_pct")
    if hit is not None:
        tail.append(f"🗄 {hit}")

    # limits (📊): provider usage windows, present when core can resolve them
    # (oauth providers). Absent for api-key/unmapped providers — segment is
    # simply omitted. Each window renders a fill bar + compact reset time.
    limits = ctx.get("limits") or {}
    windows = limits.get("windows") or []
    if limits.get("available") and windows:
        segs = [f"{bar(w.get('used_pct'))}{dur(w.get('resets_in_s'))}" for w in windows]
        tail.append("📊 " + " ".join(segs))

    print(f"{head} | " + " | ".join(tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
