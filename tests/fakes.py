from types import SimpleNamespace


class FakeClaude:
    """Stands in for anthropic.Anthropic(): answers by keyword, records every request."""

    def __init__(self, answers: dict[str, str], stop_reason: str = "end_turn"):
        self.answers = answers
        self.stop_reason = stop_reason
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def questions(self) -> list[str]:
        return [c["messages"][0]["content"][-1]["text"] for c in self.calls]

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        question = kwargs["messages"][0]["content"][-1]["text"].lower()
        text = next((a for k, a in self.answers.items() if k.lower() in question), "UNKNOWN")
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])
