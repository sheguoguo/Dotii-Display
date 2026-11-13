#!/usr/bin/env python3
"""把 DeepSeek Harness 的会话日志读成设备认识的快照。

数据源：``$DSH_HOME/sessions/<workspace>/<session>/session.v4.jsonl.zstd``
（DSH 的 append-only 会话事件流，逐行 JSON，zstd 压缩）

产物与 ``codex_app_server.CodexAppServerSource.snapshot()`` 同形，因此下游的
``apply_workbuddy_to_codex`` / ``dotii_state`` / 设备 JSON 序列化都不用改。

解压用 Python 3.14 的 stdlib ``compression.zstd``：服务器上是 3.14，够用。
Windows 本机 3.13 没有这个模块，导入时给出明确报错而不是崩在 traceback 里。
"""

from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

SESSION_GLOB = "sessions/*/*/session.v4.jsonl.zstd"
COLLECTOR_INTERVAL_SECONDS = 2.0
# turn/end 之后多久算"刚完成"，超过就当空闲。设备侧状态要跟着变化。
COMPLETED_WINDOW_SECONDS = 180

TOOL_LABELS = {
    "bash": "执行命令",
    "shell": "执行命令",
    "read": "读取文件",
    "read_file": "读取文件",
    "write": "写入文件",
    "write_file": "写入文件",
    "edit": "修改文件",
    "apply_patch": "修改文件",
    "glob": "查找文件",
    "grep": "搜索内容",
    "web_search": "联网搜索",
    "web_fetch": "抓取网页",
    "todo_write": "整理计划",
    "task": "派发子任务",
}


class DshUnavailable(RuntimeError):
    """会话日志读不了（没装 DSH、没跑过、stdlib 缺 zstd）。"""


def _zstd_decompress(data: bytes) -> bytes:
    try:
        from compression import zstd  # Python 3.14+
    except ImportError as error:  # pragma: no cover - 取决于运行环境
        raise DshUnavailable(
            "当前 Python 没有 compression.zstd（需要 3.14+）"
        ) from error
    return zstd.decompress(data)


def _epoch_seconds(value: Any) -> int:
    """DSH 的时间戳是毫秒；统一转成秒。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0
    number = float(value)
    if number > 10_000_000_000:
        number /= 1000.0
    return max(0, int(number))


def _event_time(event: dict[str, Any]) -> int:
    return _epoch_seconds(event.get("time") or event.get("timestamp"))


def _text_of(value: Any) -> str:
    """从 DSH 的 content 结构里掏出纯文本，附件/图片一律跳过。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_text_of(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if not isinstance(value, dict):
        return ""
    item_type = str(value.get("type", ""))
    if item_type in {"file", "image", "audio", "attachment"}:
        return ""
    for key in ("text", "message", "content", "value"):
        if key in value:
            text = _text_of(value[key])
            if text:
                return text
    return ""


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    raw = _zstd_decompress(path.read_bytes())
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def newest_session(dsh_home: Path) -> Path | None:
    candidates = glob.glob(str(dsh_home / SESSION_GLOB))
    if not candidates:
        return None
    return Path(max(candidates, key=lambda item: os.path.getmtime(item)))


def _session_header(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in events:
        if event.get("type") == "session":
            return event
    return {}


def build_task(events: list[dict[str, Any]], path: Path, now: int | None = None) -> dict[str, Any]:
    """把一条会话的事件流压成设备认识的 task 字典。"""
    now = now or int(time.time())
    header = _session_header(events)
    thread_id = str(header.get("id") or path.parent.name)
    started_at = _epoch_seconds(header.get("createdAt"))

    title = ""
    turn_number = 0
    turn_started_at = 0
    turn_closed = False
    current_action = ""
    last_user_message = ""
    last_assistant_message = ""
    user_messages = 0
    assistant_messages = 0
    step_count = 0
    model = ""
    provider = ""

    for event in events:
        kind = str(event.get("type", ""))
        data = event.get("data")
        if not isinstance(data, dict):
            data = {}
        when = _event_time(event)
        if kind == "session/title":
            title = str(data.get("title") or "").strip() or title
        elif kind == "request/context":
            model = str(data.get("model") or model)
            provider = str(data.get("provider") or provider)
        elif kind == "user/message":
            # DSH 会把运行时记忆、系统提示也塞成 user/message，
            # 只有 source.kind == "user" 才是人真的敲进去的那句话。
            source = data.get("source")
            source_kind = str(source.get("kind") or "") if isinstance(source, dict) else ""
            if source_kind and source_kind != "user":
                continue
            text = _text_of(data.get("content"))
            if text:
                last_user_message = text
                user_messages += 1
        elif kind == "assistant/message":
            text = _text_of(data.get("content") or data.get("message"))
            if text:
                last_assistant_message = text
            assistant_messages += 1
        elif kind == "turn/start":
            turn_number = int(data.get("turn") or turn_number + 1)
            turn_started_at = when or now
            turn_closed = False
            current_action = ""
        elif kind == "turn/end":
            turn_closed = True
            current_action = ""
        elif kind == "step/start":
            step_count += 1
            turn_closed = False
        elif kind == "tool/call":
            name = str(data.get("name") or "").strip()
            current_action = TOOL_LABELS.get(name, name) or "执行中"
            turn_closed = False

    if not turn_closed and current_action == "" and step_count:
        current_action = "思考中"

    closed_at = _event_time(events[-1]) if events else 0
    if turn_closed:
        status = "completed" if now - closed_at <= COMPLETED_WINDOW_SECONDS else "idle"
    elif turn_started_at or step_count:
        status = "working"
    else:
        status = "idle"

    duration = 0
    if status == "working" and turn_started_at:
        duration = max(0, now - turn_started_at)

    return {
        "status": status,
        "thread_id": thread_id,
        "turn_id": str(turn_number) if turn_number else "",
        "title": title or (Path(str(header.get("cwd") or "")).name if header.get("cwd") else "DeepSeek Harness"),
        "last_user_message": last_user_message[:1536],
        "last_assistant_message": last_assistant_message[:256],
        "reasoning_summary": "",
        "conversation_mode": "progress" if status == "working" else
                             ("final" if status == "completed" else "empty"),
        "codex_messages": [],
        "current_action": current_action or ("空闲" if status != "working" else "工作中"),
        "duration_seconds": duration,
        "user_message_count": user_messages,
        "message_count": user_messages + assistant_messages,
        "token_count": 0,
        "token_count_available": False,
        "plan_completed": 0,
        "plan_total": 0,
        "started_at_epoch": started_at,
        "last_updated_epoch": closed_at,
        "cwd": str(header.get("cwd") or ""),
        "model": model,
        "provider": provider,
    }


def empty_account() -> dict[str, Any]:
    """DSH 没有 Codex 那种 5 小时/每周额度概念，全部标记为不可用。"""
    return {
        "five_hour_available": False,
        "five_hour_remaining_percent": 0,
        "five_hour_reset_date": "",
        "weekly_available": False,
        "weekly_remaining_percent": 0,
        "weekly_used_percent": 0,
        "reset_date": "",
        "weekly_tokens_available": False,
        "weekly_tokens": 0,
        "weekly_tokens_as_of": "",
        "plan_type": "dsh",
        "rate_limit_reached": "",
    }


def build_snapshot(dsh_home: Path, now: int | None = None) -> dict[str, Any]:
    path = newest_session(dsh_home)
    if path is None:
        raise DshUnavailable(f"{dsh_home} 下找不到会话日志")
    events = read_events(path)
    task = build_task(events, path, now)
    return {
        "schema_version": 1,
        "generated_at_epoch": int(time.time()),
        "preview_data": False,
        "source": "dsh_session",
        "codex": {**empty_account(), "task": task, "tasks": [task]},
    }


def run_dsh_collector(
    dsh_home: Path,
    stop: Any,
    publish: Callable[[dict[str, Any]], None],
    report: Callable[[str, str], None] | None = None,
    enabled: Callable[[], bool] | None = None,
    interval: float = COLLECTOR_INTERVAL_SECONDS,
) -> None:
    """常驻轮询：把最新会话压成快照推给 store。"""
    last_error = ""
    while not stop.is_set():
        if enabled is not None and not enabled():
            if report is not None:
                report("disabled", "Codex 模块已关闭")
            stop.wait(0.5)
            continue
        try:
            publish(build_snapshot(dsh_home))
            if last_error and report is not None:
                report("online", "DeepSeek Harness 会话日志已连接")
            last_error = ""
            if report is not None:
                report("online", "数据采集正常")
        except DshUnavailable as error:
            if str(error) != last_error:
                last_error = str(error)
                if report is not None:
                    report("error", str(error))
        except (OSError, ValueError) as error:
            if str(error) != last_error:
                last_error = str(error)
                if report is not None:
                    report("error", str(error))
        deadline = time.monotonic() + max(0.5, interval)
        while not stop.is_set() and time.monotonic() < deadline:
            stop.wait(0.25)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="把 DSH 会话日志读成设备快照")
    parser.add_argument("--dsh-home", type=Path, default=Path.home() / ".dsh")
    parser.add_argument("--dump", action="store_true", help="打印一次快照就退出")
    arguments = parser.parse_args()
    snapshot = build_snapshot(arguments.dsh_home)
    if arguments.dump:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
