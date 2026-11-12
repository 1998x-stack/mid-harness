# tiny_harness/_core.py
import importlib.util
import asyncio
import logging
from pathlib import Path
from types import ModuleType
from collections.abc import AsyncIterator

from mid_harness._config import AgentConfig, Prompt
from mid_harness._messages import MessageManager
from mid_harness._llm import AnthropicProvider, OpenAIProvider, ToolCallRequest
from mid_harness._tools import ToolRegistry, ToolExecutor, ToolCollisionError
from mid_harness._guard import FilesystemGuard
from mid_harness._events import EventBus, StreamEvent
from mid_harness._loop import AgentLoop
from mid_harness._persist import SessionStore
from mid_harness._hooks import HookSystem, HookContext
from mid_harness._mcp import MCPManager
from mid_harness._skills import ProgressiveSkillLoader


class SkillLoadError(Exception):
    """Raised when a skill fails to load."""

    pass


class Agent:
    def __init__(
        self, prompt: Prompt, config: AgentConfig, store: SessionStore | None = None
    ):
        self._config = config
        self._prompt = prompt
        self._messages = MessageManager(prompt)
        self._llm_provider = self._create_provider(config)
        self._tool_registry = ToolRegistry()
        self._guard = FilesystemGuard(config.workspace)
        self._tool_executor = ToolExecutor(
            self._tool_registry,
            self._guard,
            config.timeout_ms,
            config.max_tool_result_chars,
        )
        self._event_bus = EventBus()
        self._hooks = HookSystem()
        self._mcp = MCPManager(self)
        self._loaded_skills: list[str] = []
        self._running = False
        self._store = store
        self._session_id: str | None = None
        self._chat_id = 0
        self._agent_init_emitted = False

    def _create_provider(self, config: AgentConfig):
        match config.provider:
            case "anthropic":
                return AnthropicProvider(
                    config.api_key, config.model, max_tokens=config.max_tokens
                )
            case "openai" | "deepseek":
                base_url = config.api_base_url or "https://api.openai.com/v1"
                return OpenAIProvider(
                    config.api_key,
                    config.model,
                    base_url=base_url,
                    max_tokens=config.max_tokens,
                )
            case _:
                raise ValueError(f"Unknown provider: {config.provider}")

    @property
    def tools(self) -> ToolRegistry:
        return self._tool_registry

    @property
    def events(self) -> EventBus:
        return self._event_bus

    @property
    def mcp(self):
        return self._mcp

    def on(self, event_type: str, handler) -> None:
        async def filtered(event: StreamEvent):
            if event.type == event_type:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result

        self._event_bus.subscribe(filtered)

    def hook(
        self,
        event: str,
        handler,
        *,
        hook_type: str = "post",
        priority: int = 0,
        name: str | None = None,
    ) -> str:
        """Register a lifecycle hook. Returns the hook name."""
        return self._hooks.register(
            event,
            handler,
            hook_type=hook_type,
            priority=priority,
            name=name,
            source="user",
        )

    def unhook(self, name: str) -> None:
        """Remove a hook by name."""
        self._hooks.unregister(name)

    def list_hooks(self) -> list[dict]:
        """Return all registered hooks with metadata for debugging."""
        return self._hooks.list_hooks()

    def load_skill(self, skill_ref: str) -> None:
        if skill_ref in self._loaded_skills:
            return

        skill_path = self._resolve_skill_path(skill_ref)
        if skill_path is not None and skill_path.is_dir() and (skill_path / "skill.md").exists():
            loader = ProgressiveSkillLoader(self)
            loader.load_skill(skill_path, skill_ref)
            return

        module = self._resolve_skill(skill_ref)
        if not hasattr(module, "register"):
            raise SkillLoadError(f"Skill '{skill_ref}' has no register() function")
        try:
            module.register(self)
        except ToolCollisionError as e:
            raise SkillLoadError(f"Skill '{skill_ref}' failed to load: {e}") from e
        self._loaded_skills.append(skill_ref)

        _log = logging.getLogger("mid_harness.core")
        try:
            loop = asyncio.get_running_loop()
            skill_ctx = HookContext(
                "skill:load",
                agent=self,
                data={
                    "skill_name": skill_ref,
                    "version": getattr(module, "SKILL_META", {}).get(
                        "version", "unknown"
                    ),
                    "tools_added": self._tool_registry.names(),
                },
            )
            loop.create_task(self._hooks.emit_post("skill:load", skill_ctx))
        except RuntimeError:
            _log.debug("No running event loop; skill:load hook deferred")

    def _resolve_skill_path(self, ref: str) -> Path | None:
        path = Path(ref)
        if path.is_dir():
            return path
        builtin = Path(__file__).parent / "skills" / ref
        if builtin.is_dir():
            return builtin
        return None

    def _resolve_skill(self, ref: str) -> ModuleType:
        try:
            return importlib.import_module(f"mid_harness.skills.{ref}")
        except ImportError:
            pass
        try:
            return importlib.import_module(ref)
        except ImportError:
            pass
        path = Path(ref)
        if path.exists():
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        raise SkillLoadError(f"Skill '{ref}' not found")

    async def _ensure_lifecycle_hooks(self) -> None:
        """Emit agent:init and session:start hooks if not yet emitted."""
        if not self._agent_init_emitted:
            self._agent_init_emitted = True
            init_ctx = HookContext(
                "agent:init",
                agent=self,
                data={
                    "config": {
                        "model": self._config.model,
                        "provider": self._config.provider,
                        "workspace": self._config.workspace,
                    },
                    "tools": self._tool_registry.names(),
                    "skills": list(self._loaded_skills),
                },
            )
            await self._hooks.emit_post("agent:init", init_ctx)

        if self._session_id is None:
            self._session_id = f"ses_{id(self):x}"
            session_ctx = HookContext(
                "session:start",
                agent=self,
                data={
                    "session_id": self._session_id,
                    "model": self._config.model,
                    "provider": self._config.provider,
                },
            )
            await self._hooks.emit_post("session:start", session_ctx)

    async def run(self, prompt: str) -> str:
        if self._running:
            raise RuntimeError("Agent is already running a task")

        await self._ensure_lifecycle_hooks()

        self._running = True
        self._save_turn("user", content=prompt)
        try:
            loop = AgentLoop(
                self._config,
                self._messages,
                self._llm_provider,
                self._tool_executor,
                self._event_bus,
                hooks=self._hooks,
                agent=self,
            )
            result = await loop.run(prompt)
            self._save_turn("assistant", content=result)
            return result
        finally:
            self._running = False

    async def run_stream(self, prompt: str) -> AsyncIterator[StreamEvent]:
        if self._running:
            raise RuntimeError("Agent is already running a task")

        await self._ensure_lifecycle_hooks()

        self._running = True
        self._save_turn("user", content=prompt)
        queue: list[StreamEvent] = []

        async def collector(event: StreamEvent):
            queue.append(event)

        self._event_bus.subscribe(collector)
        result_text = ""
        try:
            loop = AgentLoop(
                self._config,
                self._messages,
                self._llm_provider,
                self._tool_executor,
                self._event_bus,
                hooks=self._hooks,
                agent=self,
            )
            task = asyncio.create_task(loop.run(prompt))
            last_yielded = 0
            while not task.done() or last_yielded < len(queue):
                while last_yielded < len(queue):
                    event = queue[last_yielded]
                    if event.type == "text_delta" and event.content:
                        result_text += event.content
                    yield event
                    last_yielded += 1
                if not task.done():
                    await asyncio.sleep(0.01)
            await task
            self._save_turn("assistant", content=result_text if result_text else None)
        finally:
            self._running = False

    def clear(self) -> None:
        self._messages.clear()

    def start_session(self, session_id: str | None = None) -> str:
        if self._session_id is not None:
            return self._session_id
        if self._store is None:
            self._store = SessionStore()
        self._session_id = session_id or self._store.new_session()
        return self._session_id

    def resume_session(self, session_id: str) -> int:
        """Reload a conversation from a saved session. Returns number of turns restored."""
        if self._store is None:
            raise RuntimeError(
                "No SessionStore configured. Pass store= to Agent() or call start_session() first."
            )
        records = self._store.load_session(session_id)
        if not records:
            raise RuntimeError(f"Session '{session_id}' not found or empty.")
        self.clear()
        self._session_id = session_id
        self._chat_id = 0
        for rec in records:
            self._chat_id += 1
            role = rec["role"]
            content = rec.get("content")
            tool_calls = rec.get("tool_calls")
            tool_results = rec.get("tool_results")
            if role == "user":
                if content:
                    self._messages.add_user(content)
            elif role == "assistant":
                tc_list = None
                if tool_calls:
                    tc_list = [
                        ToolCallRequest(
                            id=tc.get("id", f"call_{i}"),
                            name=tc["name"],
                            arguments=tc.get("arguments", {}),
                        )
                        for i, tc in enumerate(tool_calls)
                    ]
                self._messages.add_assistant(text=content, tool_calls=tc_list)
                if tool_results:
                    for tr in tool_results:
                        tr_id = tr.get("id", "")
                        tr_content = tr.get("content", "")
                        self._messages.add_tool_result(tr_id, tr_content)
        return len(records)

    def _save_turn(
        self,
        role: str,
        content: str | None = None,
        tool_calls: list | None = None,
        tool_results: list | None = None,
        token_usage: dict | None = None,
    ) -> None:
        if self._store is None or self._session_id is None:
            return
        self._chat_id += 1
        self._store.save_turn(
            session_id=self._session_id,
            chat_id=self._chat_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_results=tool_results,
            token_usage=token_usage,
            model=self._config.model,
        )

    def _dump_conversation(self) -> int:
        if self._store is None or self._session_id is None:
            return 0
        messages = self._messages.to_list()
        count = 0
        i = 1

        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "")

            if role == "user":
                count += 1
                self._store.save_turn(
                    session_id=self._session_id,
                    chat_id=count,
                    role="user",
                    content=msg.get("content", ""),
                    model=self._config.model,
                )
                i += 1

            elif role == "assistant":
                raw_tool_calls = msg.get("tool_calls")
                tool_calls = None
                if raw_tool_calls:
                    tool_calls = [
                        {
                            "name": tc.get("function", {}).get("name", "?"),
                            "arguments": tc.get("function", {}).get("arguments", {}),
                        }
                        for tc in raw_tool_calls
                    ]
                tool_results = []
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool":
                    tr = messages[j]
                    tool_results.append(
                        {
                            "id": tr.get("tool_call_id", ""),
                            "content": tr.get("content", ""),
                        }
                    )
                    j += 1

                count += 1
                self._store.save_turn(
                    session_id=self._session_id,
                    chat_id=count,
                    role="assistant",
                    content=msg.get("content"),
                    tool_calls=tool_calls,
                    tool_results=tool_results if tool_results else None,
                    model=self._config.model,
                )
                i = j

            elif role == "tool":
                i += 1

            else:
                i += 1

        self._chat_id = count
        return count

    @property
    def max_iterations(self) -> int:
        return self._config.max_iterations

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def store(self) -> SessionStore | None:
        return self._store

    def estimate_tokens(self) -> int:
        return self._messages.estimate_tokens()
