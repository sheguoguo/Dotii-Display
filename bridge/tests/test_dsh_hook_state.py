"""dsh-hooks 推来的事件要压成设备认得的 task。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsh_hook_state import (
    HarnessHookStore,
    apply_event,
    build_task,
    effective_status,
    initial_state,
)


NOW = 1_800_000_000


def state_after(*events):
    state = initial_state()
    for event, context in events:
        state = apply_event(state, event, context, NOW)
    return state


class ApplyEventTests(unittest.TestCase):
    def test_turn_start_marks_working_and_keeps_the_prompt(self):
        state = state_after((
            "turn/start",
            {"sessionId": "s-1", "sessionName": "改灯效", "turn": 3,
             "content": "帮我改个灯效"},
        ))
        self.assertEqual(state["status"], "working")
        self.assertEqual(state["thread_id"], "s-1")
        self.assertEqual(state["turn_id"], "3")
        self.assertEqual(state["title"], "改灯效")
        self.assertEqual(state["last_user_message"], "帮我改个灯效")
        self.assertEqual(state["user_message_count"], 1)
        self.assertEqual(state["current_action"], "正在思考")

    def test_tool_call_becomes_a_readable_action(self):
        state = state_after(
            ("turn/start", {"turn": 1}),
            ("tool/call", {"tool": "apply_patch"}),
        )
        self.assertEqual(state["status"], "working")
        self.assertEqual(state["current_action"], "正在修改代码")

    def test_unknown_tool_falls_back_to_a_generic_label(self):
        state = state_after(("tool/call", {"tool": "weird_tool_xyz"}))
        self.assertEqual(state["current_action"], "正在调用工具")

    def test_approval_switches_to_waiting_and_back(self):
        state = state_after(
            ("turn/start", {"turn": 1}),
            ("approval/asked", {"tool": "bash"}),
        )
        self.assertEqual(state["status"], "waiting_user")
        self.assertEqual(state["current_action"], "等待用户确认")
        state = apply_event(state, "approval/decided", {"approvalOutcome": "approved"}, NOW)
        self.assertEqual(state["status"], "working")

    def test_turn_end_completed_keeps_answer_and_tokens(self):
        state = state_after(
            ("turn/start", {"turn": 2}),
            ("turn/end", {
                "turn": 2, "reason": "completed", "content": "改好了",
                "durationMs": 12_000, "usageInputTokens": 900,
                "usageOutputTokens": 300,
            }),
        )
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["last_assistant_message"], "改好了")
        self.assertEqual(state["token_count"], 1200)
        self.assertEqual(state["duration_seconds"], 12)
        task = build_task(state, NOW)
        self.assertTrue(task["token_count_available"])
        self.assertEqual(task["conversation_mode"], "final")
        self.assertEqual(task["codex_messages"], ["改好了"])

    def test_error_reason_is_a_failure(self):
        state = state_after(
            ("turn/start", {"turn": 2}),
            ("turn/end", {"reason": "error", "error": "模型调用失败"}),
        )
        self.assertEqual(state["status"], "failed")
        self.assertEqual(build_task(state, NOW)["conversation_mode"], "final")

    def test_aborted_reason_goes_back_to_idle(self):
        state = state_after(
            ("turn/start", {"turn": 2}),
            ("turn/end", {"reason": "aborted"}),
        )
        self.assertEqual(state["status"], "idle")

    def test_session_disposed_clears_the_turn(self):
        state = state_after(
            ("turn/start", {"turn": 5}),
            ("session/disposed", {}),
        )
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["turn_id"], "")
        self.assertEqual(build_task(state, NOW)["conversation_mode"], "empty")

    def test_agent_error_is_a_failure(self):
        state = state_after(("agent/error", {"error": "上下文超限"}))
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["last_assistant_message"], "上下文超限")


class HoldWindowTests(unittest.TestCase):
    def test_working_decays_to_idle_after_its_window(self):
        state = state_after(("turn/start", {"turn": 1}))
        self.assertEqual(effective_status(state, NOW + 60), "working")
        self.assertEqual(effective_status(state, NOW + 1000), "idle")

    def test_completed_decays_quicker_than_waiting_user(self):
        completed = state_after(("turn/end", {"reason": "completed"}))
        self.assertEqual(effective_status(completed, NOW + 60), "completed")
        self.assertEqual(effective_status(completed, NOW + 200), "idle")

        waiting = state_after(("approval/asked", {"tool": "bash"}))
        self.assertEqual(effective_status(waiting, NOW + 600), "waiting_user")

    def test_idle_never_expires(self):
        state = initial_state()
        self.assertEqual(effective_status(state, NOW), "idle")


class TaskShapeTests(unittest.TestCase):
    def test_task_matches_the_device_schema(self):
        state = state_after(("turn/start", {"sessionId": "s", "turn": 1}))
        task = build_task(state, NOW)
        expected = {
            "status", "thread_id", "turn_id", "title", "last_user_message",
            "last_assistant_message", "reasoning_summary", "conversation_mode",
            "codex_messages", "current_action", "duration_seconds",
            "user_message_count", "message_count", "token_count",
            "token_count_available", "plan_completed", "plan_total",
            "started_at_epoch", "last_updated_epoch",
        }
        self.assertEqual(set(task), expected)
        self.assertEqual(task["status"], "working")
        self.assertEqual(task["duration_seconds"], 0)

    def test_duration_ticks_while_working(self):
        state = state_after(("turn/start", {"turn": 1}))
        task = build_task(state, NOW + 90)
        self.assertEqual(task["duration_seconds"], 90)

    def test_title_falls_back_to_the_harness_name(self):
        task = build_task(initial_state(), NOW)
        self.assertEqual(task["title"], "DeepSeek Harness")


class StoreTests(unittest.TestCase):
    def test_store_round_trips_and_ignores_a_missing_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            store = HarnessHookStore(Path(folder) / "dsh-hook.json")
            self.assertIsNone(store.current_task(NOW))
            store.ingest("turn/start", {"turn": 1, "content": "跑一下"}, NOW)
            task = store.current_task(NOW)
            self.assertEqual(task["status"], "working")
            self.assertEqual(task["last_user_message"], "跑一下")
            reread = HarnessHookStore(Path(folder) / "dsh-hook.json")
            self.assertEqual(reread.read()["status"], "working")

    def test_current_task_can_require_fresh_data(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            store = HarnessHookStore(Path(folder) / "dsh-hook.json")
            store.ingest("turn/start", {"turn": 1}, NOW)
            self.assertIsNotNone(store.current_task(0, NOW))
            self.assertIsNone(store.current_task(3600, NOW + 7200))


class SnapshotOverlayTests(unittest.TestCase):
    def test_overlay_replaces_the_task_and_drops_preview_flag(self):
        from codex_bridge import validate_snapshot
        from dsh_reader import empty_account

        snapshot = validate_snapshot({
            "schema_version": 1,
            "preview_data": True,
            "codex": {**empty_account(), "task": {"status": "working", "title": "示例"}},
        })
        state = state_after(("turn/start", {"sessionId": "s-9", "turn": 4,
                                            "sessionName": "真任务"}))
        task = build_task(state, NOW)
        snapshot["codex"]["task"] = task
        snapshot["codex"]["tasks"] = [task]
        snapshot["preview_data"] = False

        validated = validate_snapshot(snapshot)
        self.assertEqual(validated["codex"]["task"]["thread_id"], "s-9")
        self.assertEqual(validated["codex"]["task"]["status"], "working")
        self.assertFalse(validated["preview_data"])


class OutOfOrderEventTests(unittest.TestCase):
    """dsh-hooks 每个事件一个进程，落盘顺序不等于发生顺序。"""

    def test_late_tool_call_does_not_revive_a_finished_turn(self):
        state = initial_state()
        state = apply_event(state, "turn/start",
                            {"timestamp": "2026-11-16T04:50:00Z", "content": "干活"}, NOW)
        state = apply_event(state, "turn/end",
                            {"timestamp": "2026-11-16T04:50:09Z", "reason": "complete"}, NOW)
        self.assertEqual(state["status"], "completed")

        # 比 turn/end 早、但进程晚跑完的 tool/call 不能把状态改回 working
        state = apply_event(state, "tool/call",
                            {"timestamp": "2026-11-16T04:50:03Z", "tool": "bash"}, NOW)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["event"], "turn/end")

    def test_newer_event_still_applies(self):
        state = initial_state()
        state = apply_event(state, "turn/end",
                            {"timestamp": "2026-11-16T04:50:09Z", "reason": "complete"}, NOW)
        state = apply_event(state, "turn/start",
                            {"timestamp": "2026-11-16T04:51:00Z", "content": "下一轮"}, NOW)
        self.assertEqual(state["status"], "working")

    def test_missing_timestamp_falls_back_to_receive_time(self):
        state = initial_state()
        state = apply_event(state, "turn/start", {"content": "干活"}, NOW)
        self.assertEqual(state["last_event_ts_ms"], NOW * 1000)
        self.assertEqual(state["status"], "working")


if __name__ == "__main__":
    unittest.main()
