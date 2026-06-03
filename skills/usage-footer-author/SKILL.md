---
name: usage-footer-author
description: Author or modify a Usage Footer renderer script for the OpenClaw usage-footer plugin. Use when the user asks to change, tweak, sanitise, suppress, restyle, or build the per-surface usage footer (Discord/Telegram/any channel), or iterate on what data the footer shows. The footer is a user-configured local script that receives a JSON contract on stdin and prints one footer line on stdout; this skill explains that contract, the config, and the rules for editing the script safely.
---

# Usage Footer Author

The `usage-footer` plugin appends a per-response footer to OpenClaw replies. The
footer text is produced by a **local script you own** ("the renderer"). On each
final reply the plugin pipes a JSON contract to the renderer on **stdin**; the
script prints up to `maxOutputLines` lines on **stdout**, which are appended to
the reply. Any failure / timeout / empty / oversize output simply produces **no
footer** for that turn — the renderer is pure userland and cannot break a reply.

## How it is wired

The plugin is configured in `openclaw.json` under
`plugins.entries["usage-footer"].config`. A default renderer applies to every
surface; per-surface entries under `surfaces.<surface>` override it:

```jsonc
"plugins": { "entries": { "usage-footer": {
  "enabled": true,
  "config": {
    "command": "/abs/path/to/default-renderer.py",  // fallback for all surfaces
    "format": "plain",            // plain | raw | preformatted
    "timeoutMs": 3000,
    "maxOutputChars": 500,
    "maxOutputLines": 2,
    "surfaces": {
      "discord":  { "command": "/abs/path/to/discord.py",  "format": "plain" },
      "telegram": { "command": "/abs/path/to/telegram.py", "format": "raw", "maxOutputLines": 3 }
    }
  }
} } }
```

- **Channel-agnostic.** Surfaces are just keys. To add Feishu/WeChat/etc., add a
  `surfaces.<name>` entry pointing at a renderer — no plugin change.
- `enabled: false` (top-level or per-surface) disables it; a surface with no
  resolvable `command` simply renders no footer there.
- `format`: `plain` (append as-is), `raw` (append as-is, no markdown escaping),
  `preformatted` (wrap the line in a ```code``` block).
- **Inspect the live renderer before editing** — it may be a regular file or a
  symlink into an `examples/` folder; switch variants by repointing `command`
  or flipping the symlink.

## The contract (`openclaw.usageLine.v1`) — what arrives on stdin

The plugin emits this subset. Treat **every field as optional** — it may be
absent, null, or grow over time.

```jsonc
{
  "schema": "openclaw.usageLine.v1",
  "surface": "discord|telegram|...|null",

  "model": {
    "id": "...", "display_name": "...", "provider": "...",
    "reasoning": "low|medium|high|...|null",   // string
    "actual": "prov/model|null",               // resolved winner ref
    "resolved_ref": "prov/model|null",
    "is_fallback": false                       // a fallback model ran
  },

  "state": {
    "fast_mode": true|false|null,   // null=unknown; false=OFF (snail)
    "compactions": 0|null
  },

  "usage": {
    "input_tokens": 3100, "output_tokens": 820,
    "cache_read_tokens": 58000, "cache_write_tokens": 0,
    "total_tokens": 61920,
    "cache_hit_pct": 95             // cacheRead / (cacheRead+cacheWrite+input)
  },

  "context": { "used_tokens": 61100, "max_tokens": 272000, "pct_used": 22 },

  // present only for providers core can resolve usage windows for (OAuth, e.g.
  // openai/codex) AND when the plugin-SDK limits accessor is available; absent
  // for api-key / unmapped providers, leaving the renderer free to self-source.
  "limits": {
    "available": true,
    "source": "core",
    "display_name": "OpenAI",
    "windows": [
      { "label": "5h",   "used_pct": 2, "pct_left": 98, "resets_in_s": 15360 },
      { "label": "Week", "used_pct": 1, "pct_left": 99, "resets_in_s": 580800 }
    ]
  }
}
```

The `📊` limits segment needs OpenClaw built with the plugin-SDK usage-limits
accessor (openclaw/openclaw#89631). Everything else works without it.

## Hard rules for the renderer

1. Read JSON from stdin, print to stdout. Nothing else.
2. Emit at most `maxOutputLines` lines and stay under `maxOutputChars` (read the
   live caps from the plugin config). Over-limit or empty output = no footer.
3. Be fail-safe: never throw on a missing field. Use `.get()` with defaults.
4. Never invent or fake a datapoint. If a field is absent/null, show nothing for
   it. Glyphs must reflect real contract state — vibes are not telemetry.
5. Stay fast. No network, no heavy compute. The plugin enforces a timeout
   (SIGTERM→SIGKILL) but the script should be trivial.
6. The renderer is the policy engine: branch on `surface` to sanitise or restyle
   per channel. No core/config change needed. (Note: the plugin contract does
   not carry chat type, so do not branch on group-vs-DM — that information is
   not provided.)

## Presentation guidance

- Glyphs, aliases, spacing, and the limits display are user-owned presentation.
  Inspect the active renderer and the `examples/` folder for the current style.
- Keep glyphs truthful: fallback, fast-mode, cache, context, and subscription
  limits should only render when backed by a real contract field.
- Omit unavailable values. Do not infer fast-mode, fallback, or billing windows.

## Testing without bothering the user

A renderer reads one JSON object on stdin and prints up to `maxOutputLines`
lines. Test with a self-contained sample (no external files):

```bash
cat <<'JSON' | python3 discord.py
{
  "schema": "openclaw.usageLine.v1", "surface": "discord",
  "model": { "id": "gpt-5.5", "display_name": "gpt-5.5", "provider": "openai",
    "reasoning": "medium", "actual": "openai/gpt-5.5", "is_fallback": false },
  "state": { "fast_mode": false, "compactions": 0 },
  "usage": { "input_tokens": 3100, "output_tokens": 820,
    "cache_read_tokens": 58000, "cache_write_tokens": 0, "cache_hit_pct": 95 },
  "context": { "used_tokens": 61100, "max_tokens": 272000, "pct_used": 22 },
  "limits": {
    "available": true, "source": "core", "display_name": "OpenAI",
    "windows": [
      { "label": "5h", "used_pct": 2, "pct_left": 98, "resets_in_s": 15360 },
      { "label": "Week", "used_pct": 1, "pct_left": 99, "resets_in_s": 580800 }
    ]
  }
}
JSON
```

Vary the sample to exercise edge cases: `is_fallback:true`,
`state.fast_mode:false` (snail) vs `true`, `limits` absent (api-key provider),
a different `surface`. Always `python3 -m py_compile <script>.py` after editing.

## Safety / scope

- Editing the renderer script cannot break OpenClaw — worst case is no footer.
- Changing the renderer `command` path is a config edit
  (`plugins.entries.usage-footer.config…`); prefer editing the script in place
  at the existing path. Back up `openclaw.json` before config edits.
- See `examples/discord.py` in this plugin for a complete renderer covering
  every segment, including `📊`.
