"""Bridge-served responses must still produce exactly one spend-log record.

The Codex and Antigravity bridges answer requests themselves, so LiteLLM never
wraps them in CustomStreamWrapper and no success callback fires. Before this
contract existed, successful bridge completions were absent from /spend/logs
entirely while only their exceptions were recorded.
"""
import ast
import asyncio
import datetime
import sys
import types
from pathlib import Path

source = Path(__file__).with_name("sitecustomize.py").read_text()
module = ast.parse(source)
names = {"_emit_bridge_success", "_logged_bridge_stream"}
functions = [
    node for node in module.body
    if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in names
]
assert len(functions) == len(names), f"missing helpers: {names - {f.name for f in functions}}"

built = []


class FakeLitellm:
    @staticmethod
    def stream_chunk_builder(chunks, messages=None, start_time=None, end_time=None):
        built.append((tuple(chunks), start_time, end_time))
        return {"reassembled": list(chunks)}


class FakeLogging:
    def __init__(self):
        self.calls = []

    async def async_success_handler(self, result, start_time, end_time):
        self.calls.append((result, start_time, end_time))


namespace = {
    "litellm": FakeLitellm,
    "datetime": datetime,
    "sys": sys,
    "print": lambda *a, **k: None,
}
exec(compile(ast.Module(body=functions, type_ignores=[]), "sitecustomize.py", "exec"), namespace)
logged_stream = namespace["_logged_bridge_stream"]
emit_success = namespace["_emit_bridge_success"]


async def gen_chunks(items):
    for item in items:
        yield item


async def main():
    # Streaming: chunks pass through untouched and exactly one record is emitted.
    logging_obj = FakeLogging()
    start = datetime.datetime.now()
    seen = []
    async for chunk in logged_stream(gen_chunks(["a", "b", "c"]), logging_obj, [{"role": "user"}], start):
        seen.append(chunk)
    assert seen == ["a", "b", "c"], seen
    assert len(logging_obj.calls) == 1, logging_obj.calls
    result, logged_start, logged_end = logging_obj.calls[0]
    assert result == {"reassembled": ["a", "b", "c"]}, result
    assert logged_start is start and logged_end >= start

    # No logging object (direct SDK use): stream still works, nothing recorded.
    passthrough = [c async for c in logged_stream(gen_chunks(["x"]), None, [], start)]
    assert passthrough == ["x"]

    # Empty stream must not fabricate a spend record.
    empty_logger = FakeLogging()
    assert [c async for c in logged_stream(gen_chunks([]), empty_logger, [], start)] == []
    assert empty_logger.calls == []

    # A logging failure must never surface to the caller.
    class ExplodingLogging(FakeLogging):
        async def async_success_handler(self, result, start_time, end_time):
            raise RuntimeError("callback exploded")

    survived = [c async for c in logged_stream(gen_chunks(["y"]), ExplodingLogging(), [], start)]
    assert survived == ["y"]

    # Non-streaming path records the response object as-is.
    direct_logger = FakeLogging()
    await emit_success(direct_logger, {"id": "resp-1"}, start, datetime.datetime.now())
    assert len(direct_logger.calls) == 1
    assert direct_logger.calls[0][0] == {"id": "resp-1"}

    # Missing response or logger is a no-op, not a crash.
    await emit_success(None, {"id": "resp-2"}, start, datetime.datetime.now())
    noop_logger = FakeLogging()
    await emit_success(noop_logger, None, start, datetime.datetime.now())
    assert noop_logger.calls == []


asyncio.run(main())
print("Bridge spend logging contract OK")
