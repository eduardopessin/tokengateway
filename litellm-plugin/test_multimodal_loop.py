"""Multimodal on the Gemini bridge, and the reasoning loop guard.

The wire shapes here were measured against the Antigravity backend, not read
from the documentation: `inlineData` with bare base64 is accepted and
understood (with a red image the model named the colour correctly, and without
the image the same request hallucinated a different one), a PDF inlined as
`application/pdf` returned the word printed on the page, an image inside
`functionResponse.parts` was seen by every served generation, and `fileData`
with a web URL returned `404 Requested entity was not found`.

The fixture text below is kept verbatim in the language it was measured in:
the loop thresholds were calibrated against exactly these strings, so
rewording them would invalidate the numbers recorded in the comments.
"""
import ast
import random
import base64
import json
import mimetypes
import re
import sys
import collections
from pathlib import Path

MODULE = Path(__file__).with_name("sitecustomize.py")
TREE = ast.parse(MODULE.read_text())


def load(names, const_prefixes, classes=()):
    nodes = [
        node for node in TREE.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or (isinstance(node, ast.ClassDef) and node.name in classes)
        or (isinstance(node, ast.Assign) and any(
            getattr(target, "id", "").startswith(const_prefixes)
            for target in node.targets
        ))
    ]
    found = {getattr(n, "name", None) for n in nodes}
    missing = (set(names) | set(classes)) - found
    assert not missing, f"missing: {missing}"
    namespace = {
        "base64": base64, "json": json, "re": re, "mimetypes": mimetypes,
        "collections": collections, "httpx": None,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(MODULE), "exec"), namespace)
    return namespace


MEDIA = load(
    ["_google_inline_part", "_google_media_from_url", "_google_media_part",
     "_google_content_parts"],
    ("_GOOGLE_INLINE", "_GOOGLE_FETCH", "_GOOGLE_DATA_URI", "_GOOGLE_FILE_URI"),
)
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


def test_data_uri_becomes_inline_data():
    part = MEDIA["_google_media_part"]({
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{PNG}"},
    })
    # Bare base64: the `data:...` prefix in the `data` field gives a 400 on the backend.
    assert part == {"inlineData": {"mimeType": "image/png", "data": PNG}}, part


def test_file_uris_the_backend_accepts_are_passed_through():
    for uri in ("gs://bucket/doc.pdf",
                "https://generativelanguage.googleapis.com/v1beta/files/abc"):
        part = MEDIA["_google_media_from_url"](uri, "application/pdf")
        assert part == {"fileData": {"mimeType": "application/pdf", "fileUri": uri}}, part


def test_a_pdf_keeps_its_mime_type():
    part = MEDIA["_google_media_part"]({
        "type": "file",
        "file": {"filename": "relatorio.pdf", "file_data": f"data:application/pdf;base64,{PNG}"},
    })
    assert part["inlineData"]["mimeType"] == "application/pdf", part


def test_mime_is_guessed_from_the_filename_when_absent():
    part = MEDIA["_google_media_part"]({
        "type": "input_file",
        "filename": "captura.png",
        "file_data": base64.b64encode(b"x" * 80).decode(),
    })
    assert part["inlineData"]["mimeType"] == "image/png", part


def test_text_and_media_keep_their_order():
    parts = MEDIA["_google_content_parts"]([
        {"type": "text", "text": "antes"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG}"}},
        {"type": "text", "text": "depois"},
    ])
    assert [next(iter(p)) for p in parts] == ["text", "inlineData", "text"], parts


def test_oversized_media_fails_loud():
    huge = b"x" * (MEDIA["_GOOGLE_INLINE_MAX_BYTES"] + 1)
    try:
        MEDIA["_google_inline_part"]("image/png", huge)
    except Exception as exc:
        assert "exceeds the" in str(exc), exc
        return
    raise AssertionError("media above the limit passed silently")


def test_unknown_part_types_are_ignored_not_fatal():
    parts = MEDIA["_google_content_parts"]([
        {"type": "text", "text": "ok"},
        {"type": "video_url", "video_url": {"url": "https://example/a.mp4"}},
        {"not_a_dict": True},
    ])
    assert parts == [{"text": "ok"}], parts


LOOP = load(
    ["_trigrams", "_google_loop_guard"],
    ("_LOOP_",),
    classes=("_ThinkingLoopDetector", "_ThinkingLoopError"),
)


def detector():
    return LOOP["_ThinkingLoopDetector"]()


def feed_all(det, segments):
    """Feeds paragraph-separated segments, returning the first reason."""
    for segment in segments:
        reason = det.feed(segment + "\n\n")
        if reason:
            return reason
    return None


def test_verbatim_tail_repetition_trips():
    det = detector()
    chunk = "Vou verificar o ficheiro de configuracao outra vez para confirmar. " * 4
    reason = det.feed(chunk) or det.feed(chunk)
    assert reason and "verbatim repetition" in reason, reason


def test_near_duplicate_segments_trip():
    det = detector()
    base = ("Analiso o deployment para entender porque o pod nao arranca e "
            "verifico os eventos do cluster com atencao.")
    reason = feed_all(det, [base, base.replace("atencao", "cuidado")])
    assert reason and "near-duplicate" in reason, reason


def test_progress_stall_trips_after_eight_segments():
    """Stall: the same vocabulary, reordered, with no new concrete anchor.

    The fixture has to stay below the near-duplicate threshold, otherwise that
    is the detector that fires first. Distinct permutations of the same bag of
    words achieve it: trigram Jaccard measured between 0.28 and 0.44, far from
    0.8, while novelty falls to 0.00 from the 7th segment onwards.
    """
    det = detector()
    pool = (
        "verifico confirmo reviso analiso ponderando situacao contexto abordagem "
        "estrategia alternativa hipotese conclusao passo etapa momento seguinte "
        "anterior detalhe aspecto ponto questao duvida certeza garantia risco "
        "cuidado atencao calma pressa tempo ordem sequencia caminho direccao "
        "sentido proposito objectivo resultado efeito causa razao motivo"
    ).split()
    segments = []
    for index in range(14):
        shuffled = random.Random(7 + index).sample(pool, 16)
        segments.append(" ".join(shuffled) + ".")
    reason = feed_all(det, segments)
    assert reason and "no novelty" in reason, reason
    assert det.stalled == LOOP["_LOOP_STALL_SEGMENTS"], det.stalled


def test_header_runaway_trips_at_the_threshold():
    det = detector()
    reason = feed_all(det, [f"**Passo {i}**" for i in range(1, 40)])
    assert reason and "summary headers" in reason, reason
    assert det.headers == LOOP["_LOOP_HEADER_RUNAWAY"], det.headers


def test_real_reasoning_does_not_trip():
    """Reasoning that progresses has to pass; a false positive kills a good turn."""
    det = detector()
    segments = [
        "O pedido falha com 400 e a mensagem fala do campo top_p, logo comeco por "
        "confirmar o valor que foi enviado no corpo do pedido.",
        "Pelo traceback em bridge.py:412 o valor chega a 0.9, abaixo do minimo de "
        "0.95 que a API exige quando o thinking esta activo.",
        "A alternativa seria fixar 0.95, mas isso inventa uma escolha do cliente; "
        "descartar o campo mantem o comportamento previsivel.",
        "Vou editar _inject_claude_prompt para remover top_p e cobrir o caso com "
        "um teste que compara a forma final do corpo.",
        "Falta decidir se temperature segue a mesma regra; ela ja e reescrita para "
        "1.0 mais acima, portanto nao mexo nessa parte agora.",
        "Por fim corro a suite inteira e confirmo no proxy que o pedido que dava "
        "400 responde 200 com o mesmo prompt.",
        "Registo a medicao no comentario para que ninguem reponha o campo sem "
        "voltar a medir contra o upstream.",
        "Depois verifico se a mesma restricao aparece nos modelos budget, porque "
        "a mensagem de erro difere entre as duas familias.",
        "Se diferir, separo os dois ramos e documento cada um com o texto exacto "
        "devolvido pelo servidor.",
        "Termino a revisitar a lista de betas para garantir que nenhuma delas "
        "reintroduz o parametro pela porta do lado.",
    ]
    reason = feed_all(det, segments)
    assert reason is None, f"false positive: {reason}"


def test_guard_only_watches_the_monitored_family():
    guard = LOOP["_google_loop_guard"]
    assert guard("gemini-3.8-flash-low") is not None
    assert guard("claude-opus-5") is None
    assert guard("gpt-5.5") is None


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
