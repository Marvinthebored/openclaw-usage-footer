import { spawn } from "node:child_process";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { resolveLivePluginConfigObject } from "openclaw/plugin-sdk/plugin-config-runtime";

const PLUGIN_ID = "usage-footer";
const SIGKILL_GRACE_MS = 2000;

function resolveRenderer(config, surface) {
  if (!config || config.enabled === false) return null;
  const surfaceConfig = surface && config.surfaces ? config.surfaces[surface] : undefined;
  if (surfaceConfig?.enabled === false) return null;
  const merged = { ...config, ...(surfaceConfig ?? {}) };
  const command = typeof merged.command === "string" ? merged.command.trim() : "";
  if (!command) return null;
  return {
    command,
    args: Array.isArray(merged.args) ? merged.args : [],
    format: merged.format === "preformatted" || merged.format === "raw" ? merged.format : "plain",
    timeoutMs: typeof merged.timeoutMs === "number" ? merged.timeoutMs : 1500,
    maxOutputChars: typeof merged.maxOutputChars === "number" ? merged.maxOutputChars : 500,
    maxOutputLines: typeof merged.maxOutputLines === "number" ? merged.maxOutputLines : 2,
  };
}

// Build the openclaw.usageLine.v1 contract from the reply hook's usageState.
function buildContract(state, surface) {
  const usage = state.usage ?? {};
  const input = usage.input;
  const output = usage.output;
  const cacheRead = usage.cacheRead;
  const cacheWrite = usage.cacheWrite;
  const total = usage.total;

  // cache_hit_pct: cacheRead only (writes are misses being cached). Matches
  // core status-message.ts.
  const promptTotal = (cacheRead ?? 0) + (cacheWrite ?? 0) + (input ?? 0);
  const cacheHitPct =
    promptTotal > 0 ? Math.round(((cacheRead ?? 0) / promptTotal) * 100) : undefined;

  const maxTokens = state.contextTokenBudget;
  const usedTokens = promptTotal > 0 ? promptTotal : undefined;
  const pctUsed =
    maxTokens && usedTokens !== undefined ? Math.round((usedTokens / maxTokens) * 100) : undefined;

  return {
    schema: "openclaw.usageLine.v1",
    surface: surface ?? null,
    model: {
      id: state.model ?? null,
      display_name: state.model ?? null,
      provider: state.provider ?? null,
      reasoning: state.reasoningEffort ?? null,
      // openclaw.usageLine.v1 names the resolved winner ref "actual".
      actual: state.resolvedRef ?? null,
      resolved_ref: state.resolvedRef ?? null,
      is_fallback: state.fallbackUsed === true,
    },
    state: {
      fast_mode: typeof state.fastMode === "boolean" ? state.fastMode : null,
      compactions: typeof state.compactionCount === "number" ? state.compactionCount : null,
    },
    usage: {
      input_tokens: input,
      output_tokens: output,
      cache_read_tokens: cacheRead,
      cache_write_tokens: cacheWrite,
      total_tokens: total,
      cache_hit_pct: cacheHitPct,
    },
    context: {
      used_tokens: usedTokens,
      max_tokens: maxTokens,
      pct_used: pctUsed,
    },
    // 📊 provider usage windows — passed straight through from the hook's
    // usageState when core attached them (oauth providers); absent for api-key /
    // unmapped providers, leaving the renderer free to use its own source.
    ...(state.limits ? { limits: state.limits } : {}),
  };
}

function applyFormat(output, format) {
  if (format === "raw" || format === "plain") return output;
  const safe = output.replaceAll("```", "`​``");
  return "```text\n" + safe + "\n```";
}

async function runRenderer(renderer, contextObj) {
  return await new Promise((resolve) => {
    let stdout = "";
    let settled = false;
    let killTimer;
    const child = spawn(renderer.command, renderer.args, {
      stdio: ["pipe", "pipe", "ignore"],
      windowsHide: true,
    });
    // SIGTERM, escalate to SIGKILL after a grace, so a trapping renderer cannot
    // outlive the reply.
    const terminate = () => {
      try {
        child.kill("SIGTERM");
      } catch {
        /* already exited */
      }
      if (killTimer) return;
      killTimer = setTimeout(() => {
        if (child.exitCode === null && child.signalCode === null) {
          try {
            child.kill("SIGKILL");
          } catch {
            /* already exited */
          }
        }
      }, SIGKILL_GRACE_MS);
      killTimer.unref?.();
    };
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = setTimeout(() => {
      terminate();
      finish(null);
    }, renderer.timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      if (stdout.length > renderer.maxOutputChars) {
        terminate();
        finish(null);
      }
    });
    child.on("error", () => finish(null));
    // EPIPE guard: a renderer that exits before reading stdin must not throw an
    // unhandled stream error.
    child.stdin.on("error", () => {});
    child.on("close", (code) => {
      clearTimeout(killTimer);
      if (code !== 0) return finish(null);
      const normalized = stdout.replaceAll("\r\n", "\n").trim();
      if (!normalized || normalized.length > renderer.maxOutputChars) return finish(null);
      if (normalized.split("\n").length > renderer.maxOutputLines) return finish(null);
      finish(normalized);
    });
    try {
      child.stdin.end(JSON.stringify(contextObj) + "\n");
    } catch {
      /* stdin error handler covers the outcome; fallback applies */
    }
  });
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Usage Footer",
  description: "Customizable per-response usage footer rendered by a local command.",
  register(api) {
    const currentConfig = () =>
      resolveLivePluginConfigObject(
        api.runtime?.config?.current ? () => api.runtime.config.current() : undefined,
        PLUGIN_ID,
        api.pluginConfig,
      ) ?? {};

    api.on("reply_payload_sending", async (event) => {
      try {
        if (event?.kind !== "final") return;
        const config = currentConfig();
        if (config.enabled === false) return;
        const renderer = resolveRenderer(config, event?.channel);
        if (!renderer) return;
        // Build the full contract from whatever the hook provides. The renderer
        // (user output) is the only thing that depends on which fields exist, so a
        // sparse/empty usageState yields an all-optional contract, never a
        // suppressed one.
        const state = event?.usageState ?? {};

        const payload = event.payload;
        if (!payload || typeof payload.text !== "string" || payload.text.length === 0) return;

        const contract = buildContract(state, event.channel);

        const line = await runRenderer(renderer, contract);
        if (!line) return;

        return { payload: { ...payload, text: `${payload.text}\n${applyFormat(line, renderer.format)}` } };
      } catch (err) {
        api.logger?.warn?.(`usage-footer: render failed: ${String(err)}`);
        return;
      }
    });
  },
});
