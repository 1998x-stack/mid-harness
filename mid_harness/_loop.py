# mid_harness/_loop.py
import json
from collections import deque
from mid_harness._config import AgentConfig
from mid_harness._messages import MessageManager, TokenStatus
from mid_harness._events import EventBus, StreamEvent
from mid_harness._tools import ToolExecutor, ToolResult
from mid_harness._llm import ToolCallRequest
from mid_harness._hooks import HookSystem, HookContext


class ErrorBudget:
    def __init__(self, max_total: int = 10, max_consecutive: int = 3):
        self.max_total = max_total
        self.max_consecutive = max_consecutive
        self.total_errors = 0
        self.consecutive_errors = 0

    def record_error(self) -> bool:
        self.total_errors += 1
        self.consecutive_errors += 1
        return (
            self.total_errors < self.max_total
            and self.consecutive_errors < self.max_consecutive
        )

    def record_success(self) -> None:
        self.consecutive_errors = 0

    def reset(self) -> None:
        self.total_errors = 0
        self.consecutive_errors = 0


class LoopDetector:
    def __init__(self, max_repeats: int = 3):
        self.max_repeats = max_repeats
        self._recent: deque[tuple[str, str]] = deque(maxlen=20)

    def check(self, tool_name: str, args: dict) -> bool:
        signature = (tool_name, json.dumps(args, sort_keys=True))
        self._recent.append(signature)
        count = sum(1 for s in self._recent if s == signature)
        return count < self.max_repeats

    def reset(self) -> None:
        self._recent.clear()


class AgentLoop:
    def __init__(
        self,
        config: AgentConfig,
        messages: MessageManager,
        llm,
        tools: ToolExecutor,
        events: EventBus,
        hooks: HookSystem | None = None,
        agent=None,
    ):
        self._config = config
        self._messages = messages
        self._llm = llm
        self._tools = tools
        self._events = events
        self._hooks = hooks
        self._agent = agent

    async def run(self, user_prompt: str) -> str:
        self._messages.add_user(user_prompt)
        collected_text: list[str] = []
        error_budget = ErrorBudget(
            max_total=self._config.max_errors,
            max_consecutive=self._config.max_consecutive_errors,
        )
        loop_detector = LoopDetector()

        if self._hooks:
            start_ctx = HookContext(
                "loop:start",
                agent=self._agent,
                data={
                    "messages": self._messages.to_list(),
                    "tools": self._tools.get_definitions(),
                    "max_iterations": self._config.max_iterations,
                },
            )
            await self._hooks.emit_pre("loop:start", start_ctx)
            if start_ctx.is_aborted:
                return f"Loop aborted: {start_ctx.abort_reason}"
            start_ctx.finalize()
            await self._hooks.emit_post("loop:start", start_ctx)

        for iteration in range(1, self._config.max_iterations + 1):
            token_estimate = self._messages.estimate_tokens()
            token_str = (
                f"{token_estimate}"
                if token_estimate < 1000
                else f"{token_estimate // 1000}K"
            )
            await self._events.emit(
                StreamEvent(
                    type="iteration",
                    num=iteration,
                    max=self._config.max_iterations,
                    content=token_str,
                )
            )

            if self._hooks:
                iter_ctx = HookContext(
                    "loop:iteration",
                    agent=self._agent,
                    data={
                        "num": iteration,
                        "max": self._config.max_iterations,
                        "messages": self._messages.to_list(),
                        "tokens_used": token_estimate,
                    },
                )
                await self._hooks.emit_pre("loop:iteration", iter_ctx)
                if iter_ctx.is_aborted:
                    return f"Iteration aborted: {iter_ctx.abort_reason}"
                iter_ctx.finalize()
                await self._hooks.emit_post("loop:iteration", iter_ctx)

            tool_calls: list[ToolCallRequest] = []

            pre_msgs = self._messages.to_list()
            pre_tools = self._tools.get_definitions()

            if self._hooks:
                llm_pre_ctx = HookContext(
                    "llm:pre_call",
                    agent=self._agent,
                    data={
                        "messages": pre_msgs,
                        "tools": pre_tools,
                        "model": self._config.model,
                        "provider": self._config.provider,
                    },
                )
                await self._hooks.emit_pre("llm:pre_call", llm_pre_ctx)
                if llm_pre_ctx.is_aborted:
                    return f"LLM call aborted: {llm_pre_ctx.abort_reason}"
                llm_pre_data = llm_pre_ctx.finalize()
                pre_msgs = llm_pre_data.get("messages", pre_msgs)
                pre_tools = llm_pre_data.get("tools", pre_tools)

            try:
                async for chunk in self._llm.generate_stream(pre_msgs, pre_tools):
                    if chunk.type == "text_delta" and chunk.content:
                        collected_text.append(chunk.content)
                        await self._events.emit(
                            StreamEvent(type="text_delta", content=chunk.content)
                        )
                    elif chunk.type == "tool_call_end" and chunk.tool_call:
                        tool_calls.append(chunk.tool_call)
            except Exception as e:
                if self._hooks:
                    err_ctx = HookContext(
                        "llm:error",
                        agent=self._agent,
                        data={
                            "error": str(e),
                            "messages": self._messages.to_list(),
                            "model": self._config.model,
                            "attempt": iteration,
                        },
                    )
                    await self._hooks.emit_post("llm:error", err_ctx)
                await self._events.emit(
                    StreamEvent(type="error", message=f"LLM error: {e}")
                )
                return f"Agent stopped due to LLM error: {e}"

            if self._hooks:
                llm_post_ctx = HookContext(
                    "llm:post_call",
                    agent=self._agent,
                    data={
                        "response": "streamed",
                        "messages": self._messages.to_list(),
                        "usage": None,
                    },
                )
                await self._hooks.emit_post("llm:post_call", llm_post_ctx)

            if not tool_calls:
                final_text = "".join(collected_text)
                self._messages.add_assistant(
                    text=final_text if final_text else None, tool_calls=None
                )
                if self._hooks:
                    done_ctx = HookContext(
                        "loop:complete",
                        agent=self._agent,
                        data={
                            "result": final_text,
                            "iterations": iteration,
                            "total_tokens": self._messages.estimate_tokens(),
                            "tool_calls": 0,
                        },
                    )
                    await self._hooks.emit_post("loop:complete", done_ctx)
                return final_text

            self._messages.add_assistant(
                text="".join(collected_text) if collected_text else None,
                tool_calls=tool_calls,
            )
            collected_text = []

            for tc in tool_calls:
                if not loop_detector.check(tc.name, tc.arguments):
                    result = ToolResult.error(
                        tc.id,
                        f"You've called '{tc.name}' with the same arguments "
                        f"{loop_detector.max_repeats} times. Try a different approach.",
                    )
                else:
                    tool_execute = True
                    tool_args = tc.arguments
                    if self._hooks:
                        tool_pre_ctx = HookContext(
                            "tool:pre_execute",
                            agent=self._agent,
                            data={
                                "name": tc.name,
                                "args": tool_args,
                                "call_id": tc.id,
                                "risk_level": "read_only",
                            },
                        )
                        await self._hooks.emit_guard("tool:pre_execute", tool_pre_ctx)
                        if not tool_pre_ctx.is_aborted:
                            await self._hooks.emit_pre("tool:pre_execute", tool_pre_ctx)

                        if tool_pre_ctx.is_aborted:
                            result = ToolResult.error(
                                tc.id, tool_pre_ctx.abort_reason or "Aborted by hook"
                            )
                            tool_execute = False
                        else:
                            tool_args = tool_pre_ctx.finalize().get("args", tool_args)

                    if tool_execute:
                        await self._events.emit(
                            StreamEvent(
                                type="tool_start",
                                tool_name=tc.name,
                                content=json.dumps(tool_args),
                            )
                        )
                        result = await self._tools.execute(tc.name, tool_args, tc.id)
                        await self._events.emit(
                            StreamEvent(
                                type="tool_end",
                                tool_name=tc.name,
                                content=result.content[:100],
                            )
                        )

                    if self._hooks:
                        hook_event = (
                            "tool:post_execute" if result.success else "tool:error"
                        )
                        tool_post_ctx = HookContext(
                            hook_event,
                            agent=self._agent,
                            data={
                                "name": tc.name,
                                "args": tool_args,
                                "result": result.content,
                                "call_id": tc.id,
                                "duration_ms": None,
                                "error": result.content if not result.success else None,
                            },
                        )
                        await self._hooks.emit_post(hook_event, tool_post_ctx)
                        # Apply post-hook modifications to result
                        post_data = tool_post_ctx.finalize()
                        if "result" in tool_post_ctx._modifications:
                            result = ToolResult(
                                success=result.success,
                                tool_call_id=result.tool_call_id,
                                content=post_data["result"],
                            )

                if result.success:
                    error_budget.record_success()
                else:
                    if not error_budget.record_error():
                        return await self._degraded_finish(collected_text)
                self._messages.add_tool_result(result.tool_call_id, result.content)

            status = self._messages.check_context()
            if status == TokenStatus.NEAR_CAPACITY:
                await self._events.emit(
                    StreamEvent(type="error", message="Context near limit")
                )

        if self._hooks:
            done_ctx = HookContext(
                "loop:complete",
                agent=self._agent,
                data={
                    "result": None,
                    "iterations": self._config.max_iterations,
                    "total_tokens": self._messages.estimate_tokens(),
                    "max_iterations_reached": True,
                },
            )
            await self._hooks.emit_post("loop:complete", done_ctx)
        return await self._degraded_finish(collected_text)

    async def _degraded_finish(self, collected_text: list[str]) -> str:
        self._messages.add_system_notice(
            "You've reached a safety limit. Please provide your best final answer "
            "based on what you know, without using any tools."
        )
        try:
            result = await self._llm.generate(self._messages.to_list(), tools=[])
            return result.text or "".join(collected_text)
        except Exception:
            return "".join(collected_text) or "Agent stopped."
