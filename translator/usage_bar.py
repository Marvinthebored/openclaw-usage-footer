#!/usr/bin/env python3
"""Usage-footer TRANSLATOR — engine only, no content.

INPUT  : the openclaw.usageLine.v1 state contract, JSON on stdin.
TEMPLATE: a .usage_bar.json. Resolution: $USAGE_BAR_TEMPLATE -> ~/.openclaw/.usage_bar.json.
OUTPUT : one footer line on stdout. Missing/empty/error -> exit 1 (caller falls back).

This file is a TRANSLATOR, not a dictionary. It contains no glyphs, no layout, and
no default footer — only mechanisms. All *content* (which glyphs make a meter, the
segment order, the framing) is DATA in the template:

  {
    "scales": { "<name>": "<low…high glyphs>", … },   // ordered glyph vocabularies
    "output": {
      "sep": "",                                       // joiner between pieces (default "")
      "surfaces": { "discord": [ …pieces… ],           // per-channel piece lists; the
                    "telegram": [ …pieces… ] }         // selected one IS the full output
    }
  }
  (Legacy top-level "sep"/"segments"/"surfaces" still work.)

A "scale" is one ordered low→high glyph vocabulary (string or list). The meter verb
tiles it across N cells; use width 1 for a single-glyph indicator. (Legacy `ramps`/
`series` config blocks are still merged into `scales` for back-compat.)

Verbs (mechanisms), used as {path|verb:args|fallback}:
  num                3000 -> "3.0k"      (compact count)
  dur                14820 -> "4h07m"    (seconds -> reset)
  pct                96 -> "96%"
  inv                100-value complement (88 -> 12): used% -> remaining%. Pipe it
                     before another verb, e.g. {context.pct_used|inv|meter:5:braille}.
  alias:TABLE        look value up in the template's aliases[TABLE]; echo the raw
                     value unchanged if it isn't listed (case-insensitive fallback).
                     e.g. {model.display_name|alias:models}, {model.reasoning|alias:reasoning}.
  meter:WIDTH:SCALE  a 0-100 value -> WIDTH cells filled from scales[SCALE] (graded
                     boundary cell). Emits cells only — frame them in the template.
                     meter:1:SCALE = ONE glyph (single-cell indicator; was `series`).
Segment forms: text / when / map+cases / each+item — see the example template.
"""
import json
import os
import re
import sys


# --- number formatters (algorithms, not content) ----------------------------
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


def _inv(n):
    """0–100 complement: 88 -> 12. Pipe before another verb (used% -> remaining%)."""
    if n is None or n == "":
        return n
    try:
        r = 100 - max(0.0, min(100.0, float(n)))
    except (TypeError, ValueError):
        return n
    return int(r) if r == int(r) else r


def _norm(p):
    return 0.0 if p is None else max(0.0, min(100.0, float(p))) / 100.0


# --- meter mechanism (glyphs supplied by the template, not here) --------------
def _meter(p, width, ramp):
    """Fill `width` cells proportionally from `ramp` (low→high glyph string),
    grading the boundary cell. Emits cells only; the template adds any framing.
    width 1 collapses to a single graded glyph (the former `series`)."""
    if not ramp or len(ramp) < 2:
        return ""
    empty, full = ramp[0], ramp[-1]
    total = _norm(p) * width
    fullc = int(total)
    cells = [full] * min(fullc, width)
    if len(cells) < width:
        cells.append(ramp[int(round((total - fullc) * (len(ramp) - 1)))])
    while len(cells) < width:
        cells.append(empty)
    return "".join(cells[:width])


def _apply_verb(name, args, value, vocab):
    if name == "num":
        return _num(value)
    if name == "dur":
        return _dur(value)
    if name == "pct":
        return _pct(value)
    if name == "inv":
        return _inv(value)
    if name == "alias":
        table = (vocab.get("_aliases") or {}).get(args[0], {}) if args else {}
        key = str(value)
        if key in table:
            return table[key]
        return table.get(key.lower(), value)  # echo raw value if unlisted
    if name == "meter":
        width = int(args[0]) if args else 5
        ramp = vocab.get(args[1]) if len(args) > 1 else None
        return _meter(value, width, ramp)
    return str(value)


_VERB_NAMES = {"num", "dur", "pct", "inv", "alias", "meter"}


# --- template walker ----------------------------------------------------------
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


def _interp(text, ctx, vocab):
    def repl(m):
        parts = m.group(1).split("|")
        val = _get(ctx, parts[0].strip())
        ops, fallback = [], None
        for seg in (p.strip() for p in parts[1:]):
            name = seg.split(":")[0]
            if name in _VERB_NAMES:
                ops.append((name, seg.split(":")[1:]))
            else:
                fallback = seg
        if val is None or val == "":
            return fallback if fallback is not None else ""
        out = val
        for name, args in ops:
            out = _apply_verb(name, args, out, vocab)
        return str(out)

    return _TOKEN.sub(repl, text)


def _render_segment(seg, ctx, vocab):
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
        item = seg.get("item", "")
        # per-item scale cycling: item_scales=["name", …]; `*` as a meter scale
        # resolves to THIS element's scale (positional, clamped to the last name).
        names = seg.get("item_scales")
        parts = []
        for i, el in enumerate(arr):
            iv = vocab
            if names:
                iv = {**vocab, "*": vocab.get(names[min(i, len(names) - 1)])}
            r = _interp(item, el, iv)
            if r:
                parts.append(r)
        body = seg.get("join", " ").join(parts)
        if not body:
            return None
        prefix = seg.get("text", "")
        return (prefix + " " + body) if prefix else body
    if "text" in seg:
        return _interp(seg["text"], ctx, vocab) or None
    return None


def _resolve_layout(template, surface):
    """Return (sep, pieces) for this surface.
    New schema: output.surfaces.<surface> (a piece list) + output.sep (default "").
    Falls back to output.default, then to the legacy top-level surfaces/segments."""
    output = template.get("output")
    if isinstance(output, dict):
        surfaces = output.get("surfaces") or {}
        pieces = surfaces.get(surface)
        if pieces is None:
            pieces = output.get("default", [])
        return output.get("sep", ""), pieces
    # legacy: top-level surfaces.<surface>.{sep,segments} over top-level sep/segments
    ov = (template.get("surfaces") or {}).get(surface, {}) if surface else {}
    sep = ov.get("sep", template.get("sep", " "))
    return sep, ov.get("segments", template.get("segments", []))


def render(template, contract):
    sep, pieces = _resolve_layout(template, contract.get("surface"))
    # one flat scale dict; legacy ramps/series merged in for back-compat
    vocab = {
        **template.get("ramps", {}),
        **template.get("series", {}),
        **template.get("scales", {}),
    }
    vocab["_aliases"] = template.get("aliases", {})  # named lookup tables for alias:
    out = [r for r in (_render_segment(s, contract, vocab) for s in pieces) if r]
    return sep.join(out)


def _load_template():
    path = os.environ.get("USAGE_BAR_TEMPLATE") or os.path.expanduser("~/.openclaw/.usage_bar.json")
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
        return t if isinstance(t, dict) and (t.get("output") or t.get("segments")) else None
    except Exception:
        return None


def main():
    try:
        contract = json.load(sys.stdin)
    except Exception:
        return 1
    template = _load_template()
    if not template:
        return 1  # no content => no footer (fail-open; caller renders its own default)
    try:
        line = render(template, contract)
    except Exception:
        return 1
    if not line:
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
