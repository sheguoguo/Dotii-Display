#!/usr/bin/env node
/**
 * 把 dsh-hooks 的事件转给 Dotii 管理中心。
 *
 * dsh-hooks（PeterBon/dsh-hooks）在 DSH 生命周期事件上执行这个脚本，上下文通过
 * DSH_HOOK_* 环境变量传进来。这里只做两件事：把变量收拾成 JSON、POST 给本机后台
 * （POST /api/v1/admin/harness/state）。状态机的判断全在 Python 侧
 * （bridge/dsh_hook_state.py），所以改规则只改一处。
 *
 * 退出码永远是 0：hook 失败不该污染 DSH 的 hook/failed 告警。
 *
 * 手工测试：
 *   node dsh-hook-dotii.mjs --event turn/end --reason error --dry
 *   node dsh-hook-dotii.mjs --event turn/start --content "帮我改个灯效"
 */

import fs from "node:fs";
import path from "node:path";

const ENV = process.env;
const DEFAULTS = {
  port: 8788,
  timeoutMs: 1500,
};

const INT_KEYS = {
  DSH_HOOK_TURN: "turn",
  DSH_HOOK_STEP: "step",
  DSH_HOOK_DURATION_MS: "durationMs",
  DSH_HOOK_USAGE_INPUT_TOKENS: "usageInputTokens",
  DSH_HOOK_USAGE_OUTPUT_TOKENS: "usageOutputTokens",
  DSH_HOOK_USAGE_CACHE_READ_TOKENS: "usageCacheReadTokens",
  DSH_HOOK_USAGE_CACHE_WRITE_TOKENS: "usageCacheWriteTokens",
  DSH_HOOK_USAGE_REASONING_TOKENS: "usageReasoningTokens",
  DSH_HOOK_RUNNING_SUBAGENTS: "runningSubagents",
};

const TEXT_KEYS = {
  DSH_HOOK_SESSION_ID: "sessionId",
  DSH_HOOK_SESSION_NAME: "sessionName",
  DSH_HOOK_CWD: "cwd",
  DSH_HOOK_REASON: "reason",
  DSH_HOOK_TOOL: "tool",
  DSH_HOOK_TOOL_ERROR: "toolError",
  DSH_HOOK_TOOL_ERROR_REASON: "toolErrorReason",
  DSH_HOOK_CONTENT: "content",
  DSH_HOOK_STATUS: "status",
  DSH_HOOK_ERROR: "error",
  DSH_HOOK_SOURCE: "source",
  DSH_HOOK_TIMESTAMP: "timestamp",
};

const OVERRIDES = new Set([
  "event", "sessionId", "sessionName", "cwd", "reason", "tool", "toolError",
  "toolErrorReason", "content", "status", "error", "source", "timestamp",
  "turn", "step", "durationMs", "usageInputTokens", "usageOutputTokens",
  "usageCacheReadTokens", "usageCacheWriteTokens", "usageReasoningTokens",
  "runningSubagents", "port", "timeoutMs", "dry", "log",
]);

function parseArgs(argv) {
  const overrides = {};
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (!item.startsWith("--")) continue;
    const [rawKey, inline] = item.slice(2).split("=", 2);
    // 支持 --duration-ms 这种 kebab 写法，映射到 durationMs
    const key = rawKey.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (!OVERRIDES.has(key)) continue;
    let value = inline;
    if (value === undefined) {
      const next = argv[index + 1];
      if (next !== undefined && !next.startsWith("--")) {
        value = next;
        index += 1;
      } else {
        value = true;
      }
    }
    overrides[key] = value;
  }
  return overrides;
}

function buildContext(overrides) {
  const context = {};
  for (const [envKey, field] of Object.entries(TEXT_KEYS)) {
    const value = ENV[envKey];
    if (typeof value === "string" && value.trim()) context[field] = value.trim();
  }
  for (const [envKey, field] of Object.entries(INT_KEYS)) {
    const raw = ENV[envKey];
    if (raw === undefined || raw === "") continue;
    const number = Number(raw);
    if (Number.isFinite(number)) context[field] = Math.trunc(number);
  }
  for (const field of Object.keys(TEXT_KEYS).map((key) => TEXT_KEYS[key])) {
    if (typeof overrides[field] === "string") context[field] = overrides[field];
  }
  for (const field of Object.keys(INT_KEYS).map((key) => INT_KEYS[key])) {
    if (overrides[field] === undefined) continue;
    const number = Number(overrides[field]);
    if (Number.isFinite(number)) context[field] = Math.trunc(number);
  }
  return context;
}

function logLine(file, text) {
  if (!file) return;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.appendFileSync(file, `${new Date().toISOString()} ${text}\n`, "utf8");
  } catch {
    /* 日志写不进去也不该影响主流程 */
  }
}

async function main() {
  const overrides = parseArgs(process.argv.slice(2));
  const event = String(overrides.event || ENV.DSH_HOOK_EVENT || "").trim();
  if (!event) return 0;

  const context = buildContext(overrides);
  // dsh-hooks 每个事件都是独立进程，落盘顺序不保证等于发生顺序。
  // 带上毫秒时间戳，Python 侧据此丢掉晚到的旧事件。
  if (!context.timestamp) context.timestamp = Date.now();
  const payload = { event, context };
  const port = Number(overrides.port || ENV.DOTII_BRIDGE_PORT || DEFAULTS.port);
  const timeoutMs = Number(overrides.timeoutMs || DEFAULTS.timeoutMs);
  const logFile = overrides.log || ENV.DOTII_HOOK_LOG || "";

  if (overrides.dry) {
    process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
    return 0;
  }

  const url = `http://127.0.0.1:${port}/api/v1/admin/harness/state`;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) {
      logLine(logFile, `${event} -> HTTP ${response.status}`);
    }
  } catch (error) {
    logLine(logFile, `${event} -> ${error && error.name ? error.name : "error"}`);
  }
  return 0;
}

main().then(
  (code) => process.exit(code),
  () => process.exit(0),
);
