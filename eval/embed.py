"""Embed the corpus for retrieval v2 (needs a local Ollama; the outputs are committed).

    uv run --group eval python eval/embed.py                  # all four files below
    uv run --group eval python eval/embed.py compact chunks   # only some (compact, chunks, bge, bge_chunks)

Writes into eval/data/ and records each file in meta.json ("embeddings"):

- doc_vectors_compact.npy  nomic-embed-text on the compact text (core/text.py): title,
                           company, location, job type, department, first ~500 characters
                           of the description without boilerplate. One row per corpus line.
- chunk_vectors.npy        nomic-embed-text on pieces 2..n of every listing whose cleaned
                           description is longer than one piece. Piece 1 is the compact text
                           itself, so it is not stored twice. Rows follow corpus order;
                           `chunk_owners()` says which listing each row belongs to.
- doc_vectors_bge_m3.npy   bge-m3 on the compact text.
- chunk_vectors_bge_m3.npy bge-m3 on pieces 2..n, like chunk_vectors.npy.

Texts go through the app's own OllamaProvider.embed(kind="document"): nomic gets its
"search_document: " prefix, bge-m3 gets none (it was trained without instructions, see
README). Rows are L2-normalised; a matrix over 20 MB is stored as float16.

Vectors are also kept in eval/.tuning/ (git-ignored), keyed by model and text, so a text is
only ever embedded once: eval/tune.py uses the same cache for the variants it tries.
"""

import hashlib
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.common import (
    BGE_MODEL,
    CHUNK_VECTORS,
    CHUNK_VECTORS_BGE,
    DOC_VECTORS_BGE,
    DOC_VECTORS_COMPACT,
    EMBED_MODEL,
    META,
    TUNING,
    file_entry,
    load_corpus,
    normalise_rows,
    ollama_digest,
    read_json,
    require_numpy,
    save_vectors,
    update_meta,
)

EMBED_BATCH = 16
OUTPUTS = {
    "compact": (DOC_VECTORS_COMPACT, EMBED_MODEL),
    "chunks": (CHUNK_VECTORS, EMBED_MODEL),
    "bge": (DOC_VECTORS_BGE, BGE_MODEL),
    "bge_chunks": (CHUNK_VECTORS_BGE, BGE_MODEL),
}


# --- the texts (pure functions of the corpus; the app's own code) -----------------------


def boilerplate_of(docs: list[dict], min_titles: int | None = None) -> frozenset[str]:
    from cityu_jobs.core import text

    return text.repeated_paragraphs(docs, text.REPEAT_MIN_TITLES if min_titles is None else min_titles)


def compact_texts(docs: list[dict], max_chars: int | None = None, strip: bool = True, min_titles: int | None = None) -> list[str]:
    """One compact text per corpus line, exactly as the app would embed these listings."""
    from cityu_jobs.core import text

    boilerplate = boilerplate_of(docs, min_titles) if strip else frozenset()
    return [text.compact_text(d, boilerplate, text.EXCERPT_CHARS if max_chars is None else max_chars, strip) for d in docs]


def chunk_lists(docs: list[dict], size: int | None = None, overlap: int | None = None, strip: bool = True) -> list[list[str]]:
    """Every listing's pieces (core/text.chunk_texts); the first piece is its compact text at that size."""
    from cityu_jobs.core import text

    boilerplate = boilerplate_of(docs) if strip else frozenset()
    size = text.CHUNK_CHARS if size is None else size
    overlap = text.CHUNK_OVERLAP if overlap is None else overlap
    return [text.chunk_texts(d, boilerplate, size, overlap, strip) for d in docs]


def extra_chunks(chunks: list[list[str]]) -> tuple[list[str], list[int]]:
    """(pieces 2..n of every listing, flattened in corpus order; the corpus row each belongs to)."""
    texts, owners = [], []
    for row, pieces in enumerate(chunks):
        texts += pieces[1:]
        owners += [row] * (len(pieces) - 1)
    return texts, owners


def chunk_owners(docs: list[dict]) -> list[int]:
    """Corpus row of each row of chunk_vectors.npy."""
    return extra_chunks(chunk_lists(docs))[1]


def texts_sha256(texts: list[str]) -> str:
    return hashlib.sha256("\x1e".join(texts).encode("utf-8")).hexdigest()


# --- embedding with a local cache -------------------------------------------------------


def _cache_path(model: str) -> Path:
    return TUNING / f"embeddings_{model.replace(':', '_').replace('/', '_')}.npz"


def _text_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def embed_cached(model: str, texts: list[str], kind: str = "document", progress: bool = True):
    """Unit vectors (float32) for `texts`, embedding through Ollama only those not yet in the local cache."""
    np = require_numpy()
    path = _cache_path(model if kind == "document" else f"{model}_query")
    known: dict[str, int] = {}
    stored = None
    if path.exists():
        with np.load(path, allow_pickle=False) as data:
            stored = data["vectors"]
            known = {key: i for i, key in enumerate(data["keys"].tolist())}
    keys = [_text_key(t) for t in texts]
    todo = [i for i, key in enumerate(dict.fromkeys(keys)) if key not in known]
    unique = list(dict.fromkeys(keys))
    first_index = {}
    for i, key in enumerate(keys):
        first_index.setdefault(key, i)
    missing = [key for key in unique if key not in known]
    new_vectors = []
    if missing:
        from cityu_jobs.core.ai.providers import OllamaProvider

        embedder = OllamaProvider(embed_model=model)
        start = time.perf_counter()
        for begin in range(0, len(missing), EMBED_BATCH):
            batch = missing[begin : begin + EMBED_BATCH]
            new_vectors += embedder.embed([texts[first_index[key]] for key in batch], kind=kind)
            if progress and (begin // EMBED_BATCH) % 40 == 0:
                print(f"  {model}: {min(begin + EMBED_BATCH, len(missing))}/{len(missing)} texts", flush=True)
        seconds = time.perf_counter() - start
        if progress:
            print(f"  {model}: embedded {len(missing)} texts in {seconds:.1f} s ({len(missing) / max(seconds, 1e-9):.0f}/s)")
        fresh = normalise_rows(np.asarray(new_vectors, dtype=np.float64))
        for offset, key in enumerate(missing):
            known[key] = (0 if stored is None else len(stored)) + offset
        stored = fresh if stored is None else np.vstack([stored, fresh])
        TUNING.mkdir(parents=True, exist_ok=True)
        ordered = sorted(known, key=known.get)
        with open(path, "wb") as f:
            np.savez(f, keys=np.asarray(ordered), vectors=stored)
    del todo
    return np.ascontiguousarray(stored[[known[key] for key in keys]], dtype=np.float32), len(missing)


# --- the committed files ----------------------------------------------------------------


def build(name: str, docs: list[dict]) -> dict:
    from cityu_jobs.core import text

    path, model = OUTPUTS[name]
    if name.endswith("chunks"):
        texts, owners = extra_chunks(chunk_lists(docs))
        what = (f"pieces 2..n of core/text.chunk_texts (version {text.EMBED_TEXT_VERSION}: {text.CHUNK_CHARS} characters, "
                f"{text.CHUNK_OVERLAP} overlap, header on each piece)")
    else:
        texts, owners = compact_texts(docs), None
        what = f"core/text.compact_text (version {text.EMBED_TEXT_VERSION}: header + {text.EXCERPT_CHARS} characters, boilerplate removed)"
    start = time.perf_counter()
    matrix, embedded_now = embed_cached(model, texts)
    seconds = time.perf_counter() - start
    dtype = save_vectors(path, matrix)
    entry = {
        "model": model,
        "ollama_digest": ollama_digest(model),
        "text": what,
        "prefix": "search_document: " if "nomic" in model else "",
        "rows": int(matrix.shape[0]),
        "dim": int(matrix.shape[1]),
        "dtype": dtype,
        "texts_sha256": texts_sha256(texts),
        **file_entry(path),
    }
    if owners is not None:
        entry["listings_with_extra_pieces"] = len(set(owners))
    if embedded_now:
        entry["embedded_texts"] = embedded_now
        entry["embedding_seconds"] = round(seconds, 1)
    else:  # everything came from the local cache: keep the timing of the run that did the work
        previous = (read_json(META, {}).get("embeddings") or {}).get(path.name) or {}
        for key in ("embedded_texts", "embedding_seconds"):
            if key in previous:
                entry[key] = previous[key]
    print(f"{path.name}: {matrix.shape[0]} x {matrix.shape[1]} {dtype}, {path.stat().st_size / 1e6:.1f} MB")
    return entry


def main() -> None:
    names = sys.argv[1:] or list(OUTPUTS)
    unknown = [n for n in names if n not in OUTPUTS]
    if unknown:
        sys.exit(f"unknown output {unknown}; choose from {list(OUTPUTS)}")
    docs = load_corpus()
    embeddings = read_json(META, {}).get("embeddings", {})
    for name in names:
        embeddings[OUTPUTS[name][0].name] = build(name, docs)
        update_meta("embeddings", embeddings)


if __name__ == "__main__":
    main()
