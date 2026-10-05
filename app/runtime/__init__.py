from app.runtime.base import AgentRuntime, RuntimeEvent, RuntimeRequest

__all__ = [
    "AgentRuntime",
    "ClaudeAgentRuntime",
    "FakeAgentRuntime",
    "RuntimeEvent",
    "RuntimeRequest",
]


def __getattr__(name: str):
    if name == "ClaudeAgentRuntime":
        from app.runtime.claude import ClaudeAgentRuntime

        return ClaudeAgentRuntime
    if name == "FakeAgentRuntime":
        from app.runtime.fake import FakeAgentRuntime

        return FakeAgentRuntime
    raise AttributeError(name)
