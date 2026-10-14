"""A stand-in for anthropic.Anthropic: same `beta.messages.parse(**kw)` surface, no network.

`responses` maps an output model's class name to a list of dicts (or callables taking
the request kwargs) that are returned in order. Every request is recorded.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Union

Resp = Union[dict, Callable[[dict], dict], str]


class FakeAnthropic:
    def __init__(self, responses: dict[str, list[Resp]]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw: Any):
        self.requests.append(kw)
        model_cls = kw["output_format"]
        queue = self.responses.get(model_cls.__name__) or self.responses.get("*")
        if not queue:
            raise AssertionError(f"no fake response queued for {model_cls.__name__}")
        r = queue.pop(0)
        usage = SimpleNamespace(input_tokens=12_000, output_tokens=1_500,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        if r == "refusal":
            return SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"),
                                   parsed_output=None, usage=usage, model=kw["model"])
        data = r(kw) if callable(r) else r
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, usage=usage, model=kw["model"],
                               parsed_output=model_cls.model_validate(data))


def teardown_draft(**over) -> dict:
    d = {
        "hook": {"type": "problem_callout", "seconds": 2, "on_screen_text": "Charger not working?",
                 "visual": "close-up of a blinking charger light in a dark garage"},
        "angles": ["pain", "trust"],
        "offer": "same-week install quote",
        "cta": "book a quote",
        "timeline": [{"start": 0, "end": 2, "description": "problem shown on a garage wall unit"},
                     {"start": 2, "end": 6, "description": "electrician credential shown, then the fix"}],
        "format": "demo",
        "beats": ["problem_callout", "credential", "demo", "cta"],
        "caption_style": "bold white captions, bottom third",
        "music_energy": "medium",
        "why_it_works": "Names a specific, visible pain in the first second, then removes risk with a credential before asking.",
    }
    d.update(over)
    return d
