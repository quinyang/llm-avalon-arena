"""Tests for the OpenAI-compatible adapter using a stub client. Run with `python test_openai_compat.py`."""
from __future__ import annotations

import json
from types import SimpleNamespace

from agent import LLMAgent
from game import AvalonGame
from openai_compat import OpenAICompatClient, _parse_arguments, extract_json_object


def _resp(tool_args=None, content=None, name="propose_team"):
    calls = None
    if tool_args is not None:
        calls = [SimpleNamespace(id="call_1", function=SimpleNamespace(name=name, arguments=tool_args))]
    msg = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


class StubOpenAI:
    """Returns queued responses and records requests."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)


def _leader_setup():
    g = AvalonGame(seed=0)
    p = g.current_actor()
    return g, p


def test_happy_path_forced_tool():
    g, p = _leader_setup()
    stub = StubOpenAI([_resp(json.dumps({"private_reasoning": "r", "public_statement": "hi", "team": [p, (p + 1) % 5]}))])
    d = LLMAgent(p, "asi1", "terse", OpenAICompatClient(stub)).decide(g.public_state(), g.private_state(p), g.legal_actions(p))
    assert not d.fallback and d.retries == 0 and d.input_tokens == 100
    req = stub.requests[0]
    assert req["messages"][0]["role"] == "system"
    assert req["tool_choice"] == {"type": "function", "function": {"name": "propose_team"}}
    assert req["tools"][0]["function"]["parameters"]["properties"]["team"]["minItems"] == 2
    g.apply(p, d.action)


def test_retry_sends_tool_result_message():
    g, p = _leader_setup()
    bad = json.dumps({"private_reasoning": "r", "public_statement": "x", "team": [0, 1, 2]})
    good = json.dumps({"private_reasoning": "r2", "public_statement": "y", "team": [0, 1]})
    stub = StubOpenAI([_resp(bad), _resp(good)])
    d = LLMAgent(p, "asi1", "terse", OpenAICompatClient(stub)).decide(g.public_state(), g.private_state(p), g.legal_actions(p))
    assert not d.fallback and d.retries == 1 and d.action["team"] == [0, 1]
    second = stub.requests[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["id"] == "call_1" and second[3]["tool_call_id"] == "call_1"


def test_json_in_text_when_tool_ignored():
    g, p = _leader_setup()
    text = 'Sure! {"private_reasoning": "r", "public_statement": "s", "team": [2, 4]}'
    stub = StubOpenAI([_resp(None, content=text)])
    d = LLMAgent(p, "asi1", "terse", OpenAICompatClient(stub)).decide(g.public_state(), g.private_state(p), g.legal_actions(p))
    assert not d.fallback and d.action["team"] == [2, 4]


def test_garbage_falls_back_to_random_legal():
    g, p = _leader_setup()
    stub = StubOpenAI([_resp("not json"), _resp(None, content="I refuse")])
    d = LLMAgent(p, "asi1", "terse", OpenAICompatClient(stub)).decide(g.public_state(), g.private_state(p), g.legal_actions(p))
    assert d.fallback and d.calls == 2
    g.apply(p, d.action)


def test_auto_switch_to_json_mode():
    g, p = _leader_setup()
    prose = _resp(None, content="Let me think about this carefully... " * 50)
    good_text = _resp(None, content='<think>hmm {not json}</think> {"private_reasoning": "r", "public_statement": "s", "team": [1, 2]}')
    stub = StubOpenAI([prose, prose, good_text])
    client = OpenAICompatClient(stub)
    agent = LLMAgent(p, "asi1", "terse", client)
    d1 = agent.decide(g.public_state(), g.private_state(p), g.legal_actions(p))
    assert d1.fallback and client.mode == "json"           # two tool-less replies -> switch
    d2 = agent.decide(g.public_state(), g.private_state(p), g.legal_actions(p))
    assert not d2.fallback and d2.action["team"] == [1, 2]
    req = stub.requests[2]
    assert "tools" not in req and "OUTPUT FORMAT" in req["messages"][0]["content"]


def test_extract_prefers_object_with_required_keys():
    text = 'Example: {"a": 1}. Final: {"private_reasoning": "x", "vote": "reject"} done'
    assert extract_json_object(text, ["private_reasoning", "vote"])["vote"] == "reject"
    assert extract_json_object("no json here", ["vote"]) is None


def test_parse_arguments():
    assert _parse_arguments('{"a": 1}') == {"a": 1}
    assert _parse_arguments('```json\n{"a": 1}\n```') == {"a": 1}
    assert "_unparseable" in _parse_arguments("nope")


if __name__ == "__main__":
    tests = [(k, v) for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} tests passed")
