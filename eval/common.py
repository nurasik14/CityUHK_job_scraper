"""Paths and small file helpers shared by the eval scripts (no numpy needed here)."""

import gzip
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"
DATA = EVAL_DIR / "data"
RESULTS = EVAL_DIR / "results"

CORPUS = DATA / "corpus.jsonl.gz"
DOC_VECTORS = DATA / "doc_vectors.npy"
QUERIES = DATA / "queries.jsonl"
QUERY_VECTORS = DATA / "query_vectors.npy"
META = DATA / "meta.json"
POOL = DATA / "pool.json"
DRAFTS = DATA / "labels_fable.jsonl"  # labels with reasons; qrels.jsonl is derived from it
QRELS = DATA / "qrels.jsonl"

# Retrieval v2 (see README, "Version 2").
DOC_VECTORS_COMPACT = DATA / "doc_vectors_compact.npy"   # nomic-embed-text on the compact text
CHUNK_VECTORS = DATA / "chunk_vectors.npy"               # nomic-embed-text, pieces 2..n of long listings
DOC_VECTORS_BGE = DATA / "doc_vectors_bge_m3.npy"        # bge-m3 on the compact text
CHUNK_VECTORS_BGE = DATA / "chunk_vectors_bge_m3.npy"    # bge-m3, pieces 2..n of long listings
QUERY_VECTORS_BGE = DATA / "query_vectors_bge_m3.npy"
RERANK_SCORES = DATA / "rerank_scores.jsonl.gz"          # precomputed cross-encoder scores
POOL_TUNING = DATA / "pool_dev_tuning.json"              # extra dev candidates judged while tuning
TUNING = EVAL_DIR / ".tuning"                            # git-ignored scratch: embedding cache for tuning variants
TUNING_RESULTS = RESULTS / "tuning_dev.json"
LIVE_LATENCY = RESULTS / "live_latency.json"
V1_DIR = RESULTS / "before_v1"                           # frozen copy of the first evaluation; never written

# Only these sources are public job-board listings. AIMS and ITC STEM listings sit behind a
# university login and must never end up in the committed corpus.
PUBLIC_SOURCES = ("intern_tech", "intern_bigtech")
PUBLIC_ID_PREFIX = "intern:"

EMBED_MODEL = "nomic-embed-text"  # the embedding model of version 1
BGE_MODEL = "bge-m3"
# Which query-vector file belongs to which embedding model.
QUERY_VECTOR_FILES = {EMBED_MODEL: QUERY_VECTORS, BGE_MODEL: QUERY_VECTORS_BGE}
MAX_FLOAT32_BYTES = 20 * 1024 * 1024  # a bigger matrix is stored as float16 (see README)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to a temp file in the same folder, then rename: a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)  # mkstemp makes 0600; these are public eval files, not secrets
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dumps_line(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def write_json(path: Path, obj) -> None:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    atomic_write_bytes(path, text.encode("utf-8"))


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    """One JSON value per line. Also accepts the same values pretty-printed one after another
    (e.g. after someone ran `jq . file` over it), since that loses nothing."""
    if not path.exists():
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        text = f.read()
    decoder, values, i = json.JSONDecoder(), [], 0
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return values
        value, i = decoder.raw_decode(text, i)
        values.append(value)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    text = "".join(dumps_line(r) + "\n" for r in rows)
    atomic_write_bytes(path, text.encode("utf-8"))


def write_jsonl_gz(path: Path, rows: list[dict]) -> None:
    """Deterministic gzip: no file name and mtime=0 in the header, so equal input gives equal bytes."""
    import io

    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, compresslevel=9, mtime=0) as gz:
        for row in rows:
            gz.write((dumps_line(row) + "\n").encode("utf-8"))
    atomic_write_bytes(path, buffer.getvalue())


def load_corpus(path: Path = CORPUS) -> list[dict]:
    return read_jsonl(path)


def load_queries(path: Path = QUERIES) -> list[dict]:
    queries = read_jsonl(path)
    qids = [q["qid"] for q in queries]
    if len(set(qids)) != len(qids):
        raise SystemExit(f"duplicate qid in {path}")
    return queries


def update_meta(section: str, values: dict, path: Path = META) -> None:
    meta = read_json(path, {})
    meta[section] = values
    write_json(path, meta)


def require_numpy():
    try:
        import numpy
    except ImportError:
        sys.exit("numpy is missing: run eval scripts with `uv run --group eval ...` (see eval/README.md).")
    return numpy


def load_vectors(path: Path):
    """float32 matrix. A float16 file (used if float32 would be too big for git) is widened on load."""
    np = require_numpy()
    matrix = np.load(path)
    return matrix.astype(np.float32) if matrix.dtype != np.float32 else matrix


def save_vectors(path: Path, matrix) -> str:
    """Write a unit-row matrix as float32, or float16 if float32 would exceed MAX_FLOAT32_BYTES. Returns the dtype."""
    np = require_numpy()
    matrix = np.asarray(matrix, dtype=np.float32)
    dtype = "float32"
    if matrix.nbytes > MAX_FLOAT32_BYTES:
        matrix, dtype = matrix.astype(np.float16), "float16"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        np.save(f, matrix, allow_pickle=False)
    return dtype


def file_entry(path: Path) -> dict:
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path)}


def normalise_rows(matrix):
    np = require_numpy()
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if (norms == 0).any():
        raise ValueError("an embedding vector is all zeros")
    return (matrix / norms).astype(np.float32)


def ollama_digest(model: str = EMBED_MODEL, base_url: str = "http://localhost:11434") -> str | None:
    """The local Ollama model's digest (identifies the exact weights), or None if Ollama is not reachable."""
    import httpx

    try:
        models = httpx.get(f"{base_url}/api/tags", timeout=5).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None
    wanted = model if ":" in model else f"{model}:latest"
    for m in models:
        if m.get("name") == wanted:
            return m.get("digest")
    return None
