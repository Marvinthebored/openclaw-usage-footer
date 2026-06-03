# Usage Footer — OpenClaw plugin

Appends a customizable, per-response **usage footer** to OpenClaw replies, rendered by a **local command you control**. The plugin feeds your renderer live execution state for the turn — model, reasoning effort, fast mode, fallback, context fill, token usage, cache-hit %, and (for OAuth providers) provider usage-limit windows — as a small JSON contract on stdin. Your script prints one line; the plugin appends it.

```
🤖 gpt-5.5 ✓ med 🐌 | 📚 [⣿⡇⠐⠐⠐]272k | ↕ 3.0k/128 | 🗄 96% | 📊 [⡇⠐⠐⠐⠐]4h07m [⡇⠐⠐⠐⠐]5.2d
```

## Why a local command?

The footer's *format* is entirely yours — emoji, bars, which fields, per-surface styling. The plugin owns the plumbing (collecting state, timeouts, output caps, safe subprocess handling); your renderer owns the look. Swap renderers without touching the plugin.

## Channel-agnostic by design

The plugin hooks OpenClaw's universal reply path (`reply_payload_sending`), which fires on **every** surface — Discord, Telegram, and any future channel — including the Codex app-server harness. There is no channel-specific code in the plugin: surfaces are just keys in your config, each pointing at a renderer. Add `surfaces.feishu` / `surfaces.wechat` with a renderer script and it works; no plugin change needed.

## Authoring skill (slash command)

The plugin ships an OpenClaw **skill**, `usage-footer-author` (in [`skills/`](skills/)), which OpenClaw auto-exposes as an invocable skill-command. Invoking it loads the footer **contract + renderer-authoring rules** into the agent's context, so you can just say "tweak my footer" and the agent already knows the `openclaw.usageLine.v1` contract, the config layout, the fail-safe rules, and how to test a renderer offline — no need to re-explain any of it. Think of it as the footer's `/statusline`.

## Requirements

This plugin reads per-turn state and provider usage limits through two small additions to OpenClaw core. Until they ship in a release, run a build that includes:

- **`usageState` on the `reply_payload_sending` hook** — openclaw/openclaw#89629
- **`getProviderUsageLimits` / `getProviderUsageLimitsCached` plugin-SDK accessor** — openclaw/openclaw#89631 (only needed for the `📊` limits segment)

Everything except `📊` works with #89629 alone.

## Install

1. Drop this folder somewhere OpenClaw can load plugins from (or clone it).
2. Enable + configure in `openclaw.json`:

```jsonc
"plugins": {
  "entries": {
    "usage-footer": {
      "enabled": true,
      "config": {
        // a default renderer for all surfaces …
        "command": "/abs/path/to/examples/discord.py",
        "format": "plain",
        "timeoutMs": 3000,
        "maxOutputChars": 500,
        "maxOutputLines": 2,
        // … overridden per surface as needed:
        "surfaces": {
          "discord":  { "command": "/abs/path/to/discord.py",  "format": "plain" },
          "telegram": { "command": "/abs/path/to/telegram.py", "format": "raw", "maxOutputLines": 3 }
        }
      }
    }
  }
}
```

`format`: `plain` (append as-is), `raw` (append as-is, no markdown escaping), or `preformatted` (wrap in a code block).

## The renderer contract (`openclaw.usageLine.v1`)

Your command receives one JSON object on stdin and prints **one line** on stdout. Exit non-zero or print nothing to fall back to no footer.

```jsonc
{
  "schema": "openclaw.usageLine.v1",
  "surface": "discord",
  "model":   { "id", "display_name", "provider", "reasoning", "actual", "resolved_ref", "is_fallback" },
  "state":   { "fast_mode", "compactions" },
  "usage":   { "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens", "cache_hit_pct" },
  "context": { "used_tokens", "max_tokens", "pct_used" },
  "limits":  {                       // optional; present for OAuth providers (needs #89631)
    "available": true,
    "source": "core",
    "display_name": "OpenAI",
    "windows": [ { "label", "used_pct", "pct_left", "resets_in_s" } ]
  }
}
```

`limits` is absent for api-key / unmapped providers, leaving your renderer free to self-source (e.g. read your own cost-tracker cache).

See [`examples/discord.py`](examples/discord.py) for a complete renderer covering every segment, including `📊`.

## Safety

The plugin runs your renderer with a hard timeout (SIGTERM → SIGKILL grace), output size/line caps, and EPIPE-safe stdin — a slow or misbehaving renderer can never delay or break a reply; it just falls back to no footer.

## License

MIT — see [LICENSE](LICENSE).
