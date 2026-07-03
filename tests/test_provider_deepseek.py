from agentfactor.models.enums import TerminalStatus
from agentfactor.providers.deepseek_provider import DeepSeekProvider


class PaneHistory:
    def __init__(self, history: str) -> None:
        self.history = history
        self.sent = []

    def capture_pane(self, session_name, window_name):
        return self.history

    def send_keys(self, session_name, window_name, keys, **kwargs):
        self.sent.append((keys, kwargs))


def _provider(history: str) -> DeepSeekProvider:
    return DeepSeekProvider("tid", "session", "window", None, PaneHistory(history))


def test_deepseek_status_detection():
    assert _provider("status: processing\nThinking...").get_status() == TerminalStatus.RUNNING
    assert _provider("Type your message").get_status() == TerminalStatus.READY
    assert _provider("status: completed · tokens: 100").get_status() == TerminalStatus.READY


def test_deepseek_extracts_last_response():
    history = """
status: completed
✦ first answer
status: completed
✦ final answer line one
  line two
status: completed · tokens: 10
"""

    assert _provider(history).extract_last_message_from_history(history) == (
        "final answer line one\nline two"
    )
