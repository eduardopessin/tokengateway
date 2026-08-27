"""Usage normalisation must match @oh-my-pi/pi-ai, including cache accounting.

Reference:
  Google  - omp-google-shared.ts: promptTokenCount *includes* cached tokens, so
            input = promptTokenCount - cachedContentTokenCount and thoughts are
            counted as output.
  OpenAI  - `eRe`: cached comes from input_tokens_details.cached_tokens (falling
            back to prompt_cache_hit_tokens); input_tokens is NOT reduced.
"""
import ast
from pathlib import Path

source = Path(__file__).with_name("sitecustomize.py").read_text()
module = ast.parse(source)
wanted = {"_bridge_usage", "_google_usage", "_codex_usage"}
functions = [
    node for node in module.body
    if isinstance(node, ast.FunctionDef) and node.name in wanted
]
assert len(functions) == len(wanted), f"missing: {wanted - {f.name for f in functions}}"


class Details:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens, total_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.prompt_tokens_details = None
        self.completion_tokens_details = None


namespace = {
    "Usage": FakeUsage,
    "PromptTokensDetailsWrapper": Details,
    "CompletionTokensDetailsWrapper": Details,
}
exec(compile(ast.Module(body=functions, type_ignores=[]), "sitecustomize.py", "exec"), namespace)
google_usage = namespace["_google_usage"]
codex_usage = namespace["_codex_usage"]

# --- Google: measured live 2026-08-27 on gemini-3.7-flash-low ---
u = google_usage({
    "promptTokenCount": 33958,
    "candidatesTokenCount": 12,
    "cachedContentTokenCount": 28634,
    "thoughtsTokenCount": 40,
    "totalTokenCount": 34010,
})
# Cached tokens must not be billed twice as fresh input.
assert u.prompt_tokens == 33958 - 28634, u.prompt_tokens
# Thoughts count as output.
assert u.completion_tokens == 12 + 40, u.completion_tokens
assert u.prompt_tokens_details.cached_tokens == 28634
assert u.completion_tokens_details.reasoning_tokens == 40
assert u.cache_read_input_tokens == 28634
assert u.total_tokens == 34010

# Below the implicit-cache threshold Google omits the field entirely.
cold = google_usage({"promptTokenCount": 5318, "candidatesTokenCount": 1, "thoughtsTokenCount": 21})
assert cold.prompt_tokens == 5318
assert cold.completion_tokens == 22
assert cold.prompt_tokens_details is None
assert not hasattr(cold, "cache_read_input_tokens")

# --- OpenAI Codex: input_tokens already excludes nothing; do not subtract ---
c = codex_usage({
    "input_tokens": 9000,
    "output_tokens": 120,
    "input_tokens_details": {"cached_tokens": 7000},
    "output_tokens_details": {"reasoning_tokens": 64},
    "total_tokens": 9120,
})
assert c.prompt_tokens == 9000, c.prompt_tokens
assert c.completion_tokens == 120
assert c.prompt_tokens_details.cached_tokens == 7000
assert c.completion_tokens_details.reasoning_tokens == 64
assert c.cache_read_input_tokens == 7000

# DeepSeek-style fallback field.
d = codex_usage({"input_tokens": 100, "output_tokens": 5, "prompt_cache_hit_tokens": 40})
assert d.prompt_tokens_details.cached_tokens == 40

# Empty metadata must not crash or invent tokens.
empty = codex_usage({})
assert empty.prompt_tokens == 0 and empty.completion_tokens == 0
assert empty.total_tokens == 0
assert empty.prompt_tokens_details is None

print("Usage parity with OMP reference OK (Google + Codex)")
