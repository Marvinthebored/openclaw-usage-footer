#!/usr/bin/env python3
"""Atomic usage-footer translator.

INPUT  : the openclaw.usageLine.v1 state contract, as JSON on stdin.
TEMPLATE: a .usage_bar.json (declarative). Resolution order:
          $USAGE_BAR_TEMPLATE  ->  ~/.openclaw/.usage_bar.json  ->  built-in DEFAULT.
OUTPUT : one footer line on stdout. Empty/þerror -> exit 1 (caller falls back).

Pure data translation — no exec, no network. This is the reference implementation
for the future in-core `/usage full` renderer: feed it the contract, it walks the
template's segments, interpolates contract paths through a small fixed verb set,
and joins them. Drop-in usable as the plugin's renderer command today.

================================ ATOMS (the verb set) ========================
Used as `{path|verb:arg|fallback}` inside a segment's "text".

  num                3000 -> "3.0k", 128 -> "128"           (compact counts)
  dur                14820 -> "4h07m", 449280 -> "5.2d"      (seconds -> reset)
  pct                96 -> "96%"
  meter:WIDTH[,STYLE]   a 0-100 value -> a WIDTH-cell bar  [⣿⣿⠐⠐⠐]
                        styles: braille (default) | block | shade
  moon               a 0-100 value -> ONE char from 🌑🌒🌓🌔🌕   (new->full)
  level              a 0-100 value -> ONE char from ▁▂▃▄▅▆▇█   (8 steps)

Segment forms:
  { "text": "🤖 {model.display_name}" }                 # interpolate + literals
  { "when": "usage.cache_hit_pct", "text": "🗄 {usage.cache_hit_pct|pct}" }  # show if present
  { "map":  "state.fast_mode", "cases": { "true": "⚡", "false": "🐌" } }     # enum/bool -> glyph
  { "text": "📊", "each": "limits.windows", "item": "{used_pct|meter:5}{resets_in_s|dur}" }
Top level: { "schema", "sep", "segments": [...], "surfaces": { "telegram": {...} } }
=============================================================================="""
import json
import os
import re
import sys

# ----------------------------- atoms: number -> string -----------------------
def _num(n):
    if n is None:
        return ""
    n = float(n)
    if abs(n) >= 1000:
        v = n / 1000.0
        return f"{v:.1f}k" if abs(v) < 10 else f"{round(v)}k"
    return str(int(n))


def _dur(s):
    if s is None:
        return ""
    s = max(0, int(float(s)))
    if s >= 86400:
        return f"{s / 86400:.1f}d"
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 60}m"


def _pct(n):
    return "" if n is None else f"{int(round(float(n)))}%"


# meter: a WIDTH-cell proportional bar; the boundary cell is graded, so a 5-cell
# bar still resolves ~40 levels. ramp[0] = visible empty, ramp[-1] = full.
_RAMPS = {
    "braille": "⠐⡀⡄⡆⡇⣇⣧⣷⣿",
    "block": "░▏▎▍▌▋▊▉█",
    "shade": "░▒▓█",
}


def _meter(p, width=5, style="braille"):
    ramp = _RAMPS.get(style, _RAMPS["braille"])
    empty, full = ramp[0], ramp[-1]
    p = 0.0 if p is None else max(0.0, min(100.0, float(p))) / 100.0
    total = p * width
    fullcells = int(total)
    cells = [full] * min(fullcells, width)
    if len(cells) < width:
        idx = int(round((total - fullcells) * (len(ramp) - 1)))
        cells.append(ramp[idx])
    while len(cells) < width:
        cells.append(empty)
    return "[" + "".join(cells[:width]) + "]"


def _series(p, glyphs):
    p = 0.0 if p is None else max(0.0, min(100.0, float(p))) / 100.0
    return glyphs[min(len(glyphs) - 1, int(round(p * (len(glyphs) - 1))))]


VERBS = {
    "num": lambda v, *a: _num(v),
    "dur": lambda v, *a: _dur(v),
    "pct": lambda v, *a: _pct(v),
    "meter": lambda v, *a: _meter(v, int(a[0]) if a else 5, a[1] if len(a) > 1 else "braille"),
    "moon": lambda v, *a: _series(v, "🌑🌒🌓🌔🌕"),
    "level": lambda v, *a: _series(v, "▁▂▃▄▅▆▇█"),
}

# ----------------------------- template walker -------------------------------
def _get(ctx, path):
    cur = ctx
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


_TOKEN = re.compile(r"\{([^}]+)\}")


def _interp(text, ctx):
    """Replace {path|verb:arg|fallback} tokens. A pipe segment that names a known
    verb is applied; anything else is a literal fallback used when the value is
    absent (e.g. {identity.emoji|🤖})."""
    def repl(m):
        parts = m.group(1).split("|")
        val = _get(ctx, parts[0].strip())
        verbs, fallback = [], None
        for seg in (p.strip() for p in parts[1:]):
            name = seg.split(":")[0]
            if name in VERBS:
                verbs.append((name, seg.split(":")[1:]))
            else:
                fallback = seg
        if val is None or val == "":
            return fallback if fallback is not None else ""
        out = val
        for name, args in verbs:
            out = VERBS[name](out, *args)
        return str(out)

    return _TOKEN.sub(repl, text)


def _render_segment(seg, ctx):
    if "when" in seg:
        v = _get(ctx, seg["when"])
        if v is None or v is False or v == "":
            return None
    if "map" in seg:
        v = _get(ctx, seg["map"])
        key = str(v).lower() if isinstance(v, bool) else str(v)
        cases = seg.get("cases", {})
        return cases.get(key, cases.get("_default", "")) or None
    if "each" in seg:
        arr = _get(ctx, seg["each"]) or []
        body = seg.get("join", " ").join(r for r in (_interp(seg.get("item", ""), el) for el in arr) if r)
        if not body:
            return None
        prefix = seg.get("text", "")
        return (prefix + " " + body) if prefix else body
    if "text" in seg:
        return _interp(seg["text"], ctx) or None
    return None


def render(template, contract):
    surface = contract.get("surface")
    ov = (template.get("surfaces") or {}).get(surface, {}) if surface else {}
    sep = ov.get("sep", template.get("sep", " | "))
    segments = ov.get("segments", template.get("segments", []))
    out = [r for r in (_render_segment(s, contract) for s in segments) if r]
    return sep.join(out)


DEFAULT = {
    "schema": "openclaw.usageBar.v1",
    "sep": " | ",
    "segments": [
        {"text": "{identity.emoji|🤖} {model.display_name}"},
        {"map": "model.is_fallback", "cases": {"true": "⤵"}},
        {"when": "model.reasoning", "text": "{model.reasoning}"},
        {"map": "state.fast_mode", "cases": {"true": "⚡", "false": "🐌"}},
        {"text": "📚 {context.pct_used|meter:5}{context.max_tokens|num}"},
        {"text": "↕ {usage.input_tokens|num}/{usage.output_tokens|num}"},
        {"when": "usage.cache_hit_pct", "text": "🗄 {usage.cache_hit_pct|pct}"},
        {"text": "📊", "each": "limits.windows", "item": "{used_pct|meter:5}{resets_in_s|dur}"},
    ],
}


def _load_template():
    path = os.environ.get("USAGE_BAR_TEMPLATE") or os.path.expanduser("~/.openclaw/.usage_bar.json")
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
        return t if isinstance(t, dict) and t.get("segments") else DEFAULT
    except Exception:
        return DEFAULT


def main():
    try:
        contract = json.load(sys.stdin)
    except Exception:
        return 1
    try:
        line = render(_load_template(), contract)
    except Exception:
        return 1  # fail-safe: caller renders its default
    if not line:
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
