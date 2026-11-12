"""The length-bias measurement in eval/README.md ("Why long listings won with nomic-embed-text").

    uv run --group eval python eval/length_bias.py

Reads only committed files in eval/data/. Short = listing text under 300 characters, long = over
1,000. The first table uses the v1 vectors and the first 42 queries; the second compares the
embeddings on the same 42 queries. Random pairs use a fixed seed, so the output is the same on
every run.
"""

import sys
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.common import DATA, load_corpus
from eval.embed import chunk_owners

V1_QUERIES = 42


def unit(a):
    a = np.asarray(a, dtype=np.float32)
    return a / np.linalg.norm(a, axis=1, keepdims=True)


def mean_pair_cosine(vectors, rows, n=400, seed=0):
    pick = np.random.default_rng(seed).choice(rows, n, replace=False)
    m = vectors[pick] @ vectors[pick].T
    return float(m[np.triu_indices(n, 1)].mean())


def main() -> None:
    docs = load_corpus()
    length = np.array([len(d["text"]) for d in docs])
    short, long_ = np.where(length < 300)[0], np.where(length > 1000)[0]
    is_long = length > 1000
    qn = unit(np.load(DATA / "query_vectors.npy")[:V1_QUERIES])
    qb = unit(np.load(DATA / "query_vectors_bge_m3.npy")[:V1_QUERIES])

    v1 = unit(np.load(DATA / "doc_vectors.npy"))
    s = v1 @ qn.T
    print(f"short listings {len(short)}, long listings {len(long_)}")
    print("| | short | long |")
    print(f"| mean cosine to the queries | {s[short].mean():.3f} | {s[long_].mean():.3f} |")
    print(f"| spread within one query (std) | {s[short].std(0).mean():.3f} | {s[long_].std(0).mean():.3f} |")
    print(f"| best score per query | {s[short].max(0).mean():.3f} | {s[long_].max(0).mean():.3f} |")
    print(f"| 10th-best score per query | {np.sort(s[short], 0)[-10].mean():.3f} | {np.sort(s[long_], 0)[-10].mean():.3f} |")
    print(f"| cosine between two random listings | {mean_pair_cosine(v1, short):.3f} | {mean_pair_cosine(v1, long_):.3f} |")

    owners = np.array(chunk_owners(docs))
    print()
    print("| embedding | long listings in the vector top 10 | cosine between two short listings |")
    for name, docfile, chunkfile, q in (
        ("nomic, full text (v1)", "doc_vectors.npy", None, qn),
        ("nomic, compact text", "doc_vectors_compact.npy", None, qn),
        ("nomic, best of pieces", "doc_vectors_compact.npy", "chunk_vectors.npy", qn),
        ("bge-m3, compact text", "doc_vectors_bge_m3.npy", None, qb),
        ("bge-m3, best of pieces", "doc_vectors_bge_m3.npy", "chunk_vectors_bge_m3.npy", qb),
    ):
        d = unit(np.load(DATA / docfile))
        scores = d @ q.T
        if chunkfile:  # piece 1 is the compact text; pieces 2..n are stored separately
            np.maximum.at(scores, owners, unit(np.load(DATA / chunkfile)) @ q.T)
        top = np.argsort(-scores, axis=0, kind="stable")[:10]
        pair = "—" if chunkfile else f"{mean_pair_cosine(d, short):.3f}"
        print(f"| {name} | {is_long[top].mean() * 10:.1f} | {pair} |")


if __name__ == "__main__":
    main()
