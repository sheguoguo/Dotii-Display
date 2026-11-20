#!/usr/bin/env python3
"""把 dsh-hooks 推送的 DeepSeek Harness 事件压成设备认识的 task。

dsh-hooks（PeterBon/dsh-hooks）在 DSH 每次 turn / tool / approval 变动时执行一条
命令，把上下文写进 ``DSH_HOOK_*`` 环境变量。``dsh-hook-dotii.mjs`` 把那些变量原样
POST 到这里，本模块负责累积状态并产出与 ``dsh_reader.build_task`` 同形的字典，
于是 ``apply_workbuddy_to_codex`` / ``dotii_state`` / 设备 JSON 序列化都不用改。

和 ``dsh_reader`` 的分工：dsh_reader 轮询会话日志（滞后、需要 stdlib zstd），
这里收事件推送（实时、零依赖）。产物同形，谁新鲜谁说话。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_TITLE = "DeepSeek Harness"

# 状态在没有新事件之后还能撑多久（秒），超时退化成 idle。
# completed / failed 是终态，留一小段时间给设备把表情播完。
HOLD_SECONDS = {
    "working": 900,
    "waiting_user": 1800,
    "completed": 180,
    "failed": 300,
}

TERMINAL_STATUSES = {"completed", "failed"}
# turn/end 的 reason → 设备状态。没有 aborted 这种态，用户主动停就当空闲。
FAILED_REASONS = {"error", "blocked", "max-tokens"}
STOPPED_REASONS = {"aborted", "interrupted", "forked"}

TOOL_LABELS = {
    "bash": "正在运行命令",
    "shell": "正在运行命令",
    "exec_command": "正在运行命令",
    "read": "正在读取文件",
    "read_file": "正在读取文件",
    "write": "正在写入文件",
    "write_file": "正在写入文件",
    "edit": "正在修改代码",
    "edit_file": "正在修改代码",
    "apply_patch": "正在修改代码",
    "glob": "正在查找文件",
    "grep": "正在搜索内容",
    "web_search": "正在搜索资料",
    "web_fetch": "正在抓取网页",
    "todo_write": "正在整理计划",
    "task": "正在派发子任务",
    "mcp": "正在调用工具",
}

MAX_ACTION_CHARS = 120
MAX_MESSAGE_CHARS = 1500


def _int(value: Any, fallback: int = 0) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(float(value)))
        except ValueError:
            return fallback
    return fallback


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def tool_label(tool: Any) -> str:
    name = _text(tool).lower()
    if not name:
        return ""
    if name in TOOL_LABELS:
        return TOOL_LABELS[name]
    for prefix, label in TOOL_LABELS.items():
        if name.startswith(f"{prefix}_") or name.startswith(f"{prefix}."):
            return label
    return "正在调用工具"


def initial_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at_epoch": 0,
        "event": "",
        "status": "idle",
        "title": DEFAULT_TITLE,
        "thread_id": "",
        "turn_id": "",
        "last_user_message": "",
        "last_assistant_message": "",
        "current_action": "等待新任务",
        "user_message_count": 0,
        "token_count": 0,
        "turn_started_epoch": 0,
        "finished_at_epoch": 0,
        "duration_seconds": 0,
        "last_event_ts_ms": 0,
    }


def _validate_state(payload: Any) -> dict[str, Any]:
    state = initial_state()
    if not isinstance(payload, dict):
        return state
    for key in state:
        if key in payload:
            state[key] = payload[key]
    if state["status"] not in {"idle", "working", "waiting_user", "completed", "failed"}:
        state["status"] = "idle"
    for key in ("updated_at_epoch", "user_message_count", "token_count",
                "turn_started_epoch", "finished_at_epoch", "duration_seconds",
                "last_event_ts_ms"):
        state[key] = _int(state[key])
    for key in ("event", "title", "thread_id", "turn_id", "last_user_message",
                "last_assistant_message", "current_action"):
        state[key] = _text(state[key])
    state["title"] = state["title"] or DEFAULT_TITLE
    return state


def _event_ts_ms(raw: Any, fallback_seconds: int) -> int:
    """把 hook 带过来的时间戳统一成毫秒。认 ISO 串、秒、毫秒三种。"""
    if raw is None or raw == "":
        return fallback_seconds * 1000
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
    else:
        text = str(raw).strip()
        try:
            value = float(text)
        except ValueError:
            try:
                moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return fallback_seconds * 1000
            return int(moment.timestamp() * 1000)
    return int(value) if value >= 1e11 else int(value * 1000)


def apply_event(
    state: dict[str, Any],
    event: Any,
    context: Any = None,
    now: int | None = None,
) -> dict[str, Any]:
    """把一个 DSH 事件并进已有状态，返回新字典（不改入参）。"""
    now = now or int(time.time())
    nxt = dict(state)
    ctx = context if isinstance(context, dict) else {}
    name = _text(event)

    # dsh-hooks 每个事件都是一个独立进程，并发跑完的先后顺序不等于发生顺序：
    # 实测过 tool/call 比 turn/end 晚落盘，把"已完成"覆盖成"工作中"。
    # 所以按事件自带时间戳做单调过滤，比上一次旧的事件直接丢掉。
    event_ts = _event_ts_ms(ctx.get("timestamp"), now)
    last_ts = _int(state.get("last_event_ts_ms"))
    if last_ts > 0 and event_ts < last_ts:
        return dict(state)
    nxt["last_event_ts_ms"] = max(event_ts, last_ts)

    nxt["updated_at_epoch"] = now
    nxt["event"] = name

    session_id = _text(ctx.get("sessionId"))
    if session_id:
        nxt["thread_id"] = session_id
    session_name = _text(ctx.get("sessionName"))
    if session_name:
        nxt["title"] = session_name

    content = _text(ctx.get("content"))

    if name == "session/created":
        nxt["status"] = "idle"
        nxt["current_action"] = "等待新任务"

    elif name == "session/title":
        if content:
            nxt["title"] = content

    elif name == "session/disposed":
        nxt["status"] = "idle"
        nxt["current_action"] = "等待新任务"
        nxt["turn_id"] = ""
        nxt["finished_at_epoch"] = now

    elif name == "turn/start":
        nxt["status"] = "working"
        turn = _int(ctx.get("turn"))
        nxt["turn_id"] = str(turn) if turn else nxt.get("turn_id", "")
        nxt["turn_started_epoch"] = now
        nxt["finished_at_epoch"] = 0
        nxt["duration_seconds"] = 0
        nxt["token_count"] = 0
        nxt["current_action"] = "正在思考"
        if content:
            nxt["last_user_message"] = content[:MAX_MESSAGE_CHARS]
            nxt["user_message_count"] = _int(nxt.get("user_message_count")) + 1

    elif name == "user/message":
        if content:
            nxt["last_user_message"] = content[:MAX_MESSAGE_CHARS]
            nxt["user_message_count"] = _int(nxt.get("user_message_count")) + 1

    elif name == "step/end":
        nxt["status"] = "working"
        if not _text(nxt.get("current_action")):
            nxt["current_action"] = "正在思考"

    elif name == "tool/call":
        nxt["status"] = "working"
        label = tool_label(ctx.get("tool"))
        if label:
            nxt["current_action"] = label

    elif name == "tool/result":
        nxt["status"] = "working"
        label = tool_label(ctx.get("tool"))
        if _text(ctx.get("toolError")):
            nxt["current_action"] = "工具报错，正在重试"
        elif label:
            nxt["current_action"] = label

    elif name == "approval/asked":
        nxt["status"] = "waiting_user"
        nxt["current_action"] = "等待用户确认"

    elif name == "approval/decided":
        nxt["status"] = "working"
        nxt["current_action"] = "继续处理"

    elif name == "agent/error":
        nxt["status"] = "failed"
        nxt["current_action"] = "运行出错"
        nxt["finished_at_epoch"] = now
        detail = _text(ctx.get("error"))
        if detail:
            nxt["last_assistant_message"] = detail[:MAX_MESSAGE_CHARS]

    elif name == "agent/status":
        status = _text(ctx.get("status")).lower()
        if status in {"error", "failed"}:
            nxt["status"] = "failed"
            nxt["current_action"] = "运行出错"
        elif status in {"working", "running", "busy", "thinking"}:
            nxt["status"] = "working"
        elif status in {"idle", "ready", "stopped"} and nxt.get("status") == "working":
            nxt["status"] = "idle"
            nxt["current_action"] = "等待新任务"

    elif name == "turn/end":
        reason = _text(ctx.get("reason")).lower()
        duration = _int(ctx.get("durationMs"))
        if duration:
            nxt["duration_seconds"] = duration // 1000
        tokens = sum(
            _int(ctx.get(key)) for key in (
                "usageInputTokens", "usageOutputTokens",
                "usageCacheReadTokens", "usageCacheWriteTokens",
                "usageReasoningTokens",
            )
        )
        if tokens:
            nxt["token_count"] = tokens
        if content:
            nxt["last_assistant_message"] = content[:MAX_MESSAGE_CHARS]
        if reason in FAILED_REASONS:
            nxt["status"] = "failed"
            nxt["current_action"] = "任务失败"
            nxt["finished_at_epoch"] = now
        elif reason in STOPPED_REASONS:
            nxt["status"] = "idle"
            nxt["current_action"] = "任务已中断"
            nxt["finished_at_epoch"] = now
        else:
            nxt["status"] = "completed"
            nxt["current_action"] = "任务已完成"
            nxt["finished_at_epoch"] = now
        if not nxt.get("duration_seconds") and _int(nxt.get("turn_started_epoch")):
            nxt["duration_seconds"] = max(0, now - _int(nxt["turn_started_epoch"]))

    return nxt


def effective_status(state: dict[str, Any], now: int | None = None) -> str:
    """应用保持窗口：长时间没有新事件的活跃态退化成 idle。"""
    now = now or int(time.time())
    status = _text(state.get("status")) or "idle"
    hold = HOLD_SECONDS.get(status)
    updated = _int(state.get("updated_at_epoch"))
    if hold is not None and updated and now - updated > hold:
        return "idle"
    return status


def build_task(state: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    """产出与 ``thread_snapshot()`` 同形的 task 字典。"""
    now = now or int(time.time())
    status = effective_status(state, now)
    updated = _int(state.get("updated_at_epoch"))
    started = _int(state.get("turn_started_epoch"))
    action = _text(state.get("current_action"))
    assistant = _text(state.get("last_assistant_message"))

    if status == "idle":
        action = "等待新任务"
        duration = 0
        mode = "empty"
    elif status in {"working", "waiting_user"}:
        duration = max(0, now - started) if started else _int(state.get("duration_seconds"))
        mode = "progress"
    else:
        duration = _int(state.get("duration_seconds"))
        if not duration and started:
            duration = max(0, _int(state.get("finished_at_epoch")) - started)
        mode = "final"

    if mode == "final":
        messages = [assistant] if assistant else []
    elif mode == "progress":
        messages = [action] if action else []
    else:
        messages = []

    tokens = _int(state.get("token_count"))
    return {
        "status": status,
        "thread_id": _text(state.get("thread_id"))[:63],
        "turn_id": _text(state.get("turn_id"))[:63],
        "title": (_text(state.get("title")) or DEFAULT_TITLE)[:95],
        "last_user_message": _text(state.get("last_user_message")),
        "last_assistant_message": assistant[:255],
        "reasoning_summary": "",
        "conversation_mode": mode,
        "codex_messages": messages,
        "current_action": action[:MAX_ACTION_CHARS],
        "duration_seconds": duration,
        "user_message_count": _int(state.get("user_message_count")),
        "message_count": _int(state.get("user_message_count")),
        "token_count": tokens,
        "token_count_available": tokens > 0,
        "plan_completed": 0,
        "plan_total": 0,
        "started_at_epoch": started,
        "last_updated_epoch": updated,
    }


class HarnessHookStore:
    """把 dsh-hooks 推来的事件攒成一份状态文件。

    单独一个文件（默认 ``<runtime>/dsh-hook.json``），不碰 ``state.json``，
    这样轮询采集器怎么写都不会打架，谁新鲜由 ``current_task`` 判断。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return initial_state()
        return _validate_state(raw)

    def _write_locked(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix="dsh-hook-", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def ingest(self, event: Any, context: Any = None,
               now: int | None = None) -> dict[str, Any]:
        """收一个事件，落盘，返回新的状态字典。"""
        with self.lock:
            state = apply_event(self.read(), event, context, now)
            self._write_locked(state)
        return state

    def current_task(self, max_age: int = 0, now: int | None = None) -> dict[str, Any] | None:
        """当前 task；从没收到过事件（或已过期）时返回 None。"""
        now = now or int(time.time())
        state = self.read()
        updated = _int(state.get("updated_at_epoch"))
        if not updated:
            return None
        if max_age and now - updated > max_age:
            return None
        return build_task(state, now)
