"""Contract of the /v1/responses route and of the stream chunks the bridges emit.

Both regressions covered here were found in production and are invisible to the
chat-completions path, which is why they need their own test:

1. litellm's Responses translator only accepts ModelResponse or
   CustomStreamWrapper. The bridges returned a bare async generator, so
   `/v1/responses` with stream=true answered
   `500 Unexpected response type: <class 'async_generator'>`.

2. The final usage chunk carried `choices: []`. The
   LiteLLMCompletionStreamingIterator calls `_is_reasoning_end(chunk)`, which
   does `chunk.choices[0].delta` unguarded, so the IndexError killed the stream
   before `response.completed` was emitted and clients waited forever.
"""

import ast
import asyncio
import sys
from pathlib import Path

source = Path(__file__).with_name("sitecustomize.py").read_text()
module = ast.parse(source)

names = {
    "_usage_chunk",
    "_bridge_usage",
    "_stream_chunk_is_meaningful",
    "_bridge_stream_result",
    "_BridgeStreamWrapper",
}
nodes = [
    node for node in module.body
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
]
assert len(nodes) == len(names), f"missing: {names - {n.name for n in nodes}}"
constants = [
    node for node in module.body
    if isinstance(node, ast.Assign)
    and any(getattr(t, "id", "") == "_RESPONSES_ROUTE" for t in node.targets)
]
assert constants, "_RESPONSES_ROUTE missing"


class Delta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None, role=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls
        self.role = role


class StreamingChoices:
    def __init__(self, index=0, delta=None, finish_reason=None):
        self.index = index
        self.delta = delta
        self.finish_reason = finish_reason


class ModelResponseStream:
    def __init__(self, id=None, created=None, model=None, choices=None):
        self.id = id
        self.created = created
        self.model = model
        self.choices = choices or []


class Usage:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class CustomStreamWrapper:
    """Stands in for litellm.CustomStreamWrapper: the isinstance check the
    Responses translator performs is the whole point of the wrapper."""


namespace = {
    "Delta": Delta,
    "StreamingChoices": StreamingChoices,
    "ModelResponseStream": ModelResponseStream,
    "Usage": Usage,
    "PromptTokensDetailsWrapper": Usage,
    "CompletionTokensDetailsWrapper": Usage,
    "litellm": type("Litellm", (), {"CustomStreamWrapper": CustomStreamWrapper}),
    "contextvars": __import__("contextvars"),
    "sys": sys,
}
exec(compile(ast.Module(body=constants + nodes, type_ignores=[]), "sitecustomize.py", "exec"), namespace)

usage_chunk = namespace["_usage_chunk"]("gpt-5.5", "resp-1", 0, namespace["_bridge_usage"](7, 11))
assert usage_chunk.choices, "the usage chunk must carry a choices entry"
delta = usage_chunk.choices[0].delta
assert delta.content is None and delta.reasoning_content is None
assert usage_chunk.choices[0].finish_reason is None
assert getattr(usage_chunk, "usage").prompt_tokens == 7
print("Usage chunk keeps a guarded choices entry OK")

meaningful = namespace["_stream_chunk_is_meaningful"]
assert meaningful(ModelResponseStream(choices=[StreamingChoices(delta=Delta(content="hi"))]))
assert meaningful(ModelResponseStream(choices=[StreamingChoices(delta=Delta(reasoning_content="why"))]))
assert meaningful(ModelResponseStream(choices=[StreamingChoices(delta=Delta(tool_calls=[{"index": 0}]))]))
assert not meaningful(ModelResponseStream(choices=[StreamingChoices(delta=Delta())]))
assert not meaningful(ModelResponseStream(choices=[]))
assert not meaningful(usage_chunk)
print("Meaningful-chunk detection OK")


async def _chunks():
    yield ModelResponseStream(choices=[StreamingChoices(delta=Delta(content="a"))])
    yield ModelResponseStream(choices=[StreamingChoices(delta=Delta(content="b"))])


# Outside the Responses route the chat-completions path keeps receiving the
# generator it already handled.
plain = namespace["_bridge_stream_result"](_chunks(), "gpt-5.5", None)
assert not isinstance(plain, CustomStreamWrapper)

namespace["_RESPONSES_ROUTE"].set(True)
wrapped = namespace["_bridge_stream_result"](_chunks(), "gpt-5.5", None)
assert isinstance(wrapped, CustomStreamWrapper)
assert hasattr(wrapped, "logging_obj"), "the iterator reads logging_obj"


async def drain(stream):
    seen = []
    while True:
        try:
            chunk = await stream.__anext__()
        except StopAsyncIteration:
            return seen
        seen.append(chunk.choices[0].delta.content)


assert asyncio.run(drain(wrapped)) == ["a", "b"]
namespace["_RESPONSES_ROUTE"].set(False)
print("Responses-route stream wrapper OK")
