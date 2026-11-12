"""Optional second opinion on the best matches: a cross-encoder reranker.

Keyword and vector search each look at the query and a listing separately. A cross-encoder
reads the two *together* ("does this listing answer this query?") and gives one score. That
is more accurate but far slower, so it only re-orders the top `config.RERANK_TOP_N` results
of the first search.

The model (BAAI/bge-reranker-v2-m3, multilingual) runs locally through sentence-transformers
on the GPU if there is one, in half precision (fp16). torch and sentence-transformers are
*optional*: they live in the `rerank` dependency group (`uv sync --group rerank`). When they
are missing, `load_reranker()` logs one line and returns None, and the app simply does not
rerank. Nothing else in the app imports torch.

The reranker reads the same compact text that gets embedded (core/text.py): title, company,
location, job type, department and the start of the description.
"""

import logging
import threading
from collections.abc import Callable, Sequence

from cityu_jobs import config

log = logging.getLogger(__name__)

# (query, texts) -> one score per text, higher = better match.
Scorer = Callable[[str, Sequence[str]], list[float]]

INSTALL_HINT = "uv sync --group rerank"


class Reranker:
    """bge-reranker-v2-m3 through sentence-transformers' CrossEncoder."""

    def __init__(self, model_name: str = config.RERANK_MODEL, device: str | None = None, fp16: bool = True):
        import torch
        from sentence_transformers import CrossEncoder

        self.model_name = model_name
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.fp16 = fp16 and self.device == "cuda"  # half precision only helps (and only works well) on a GPU
        kwargs = {"model_kwargs": {"torch_dtype": torch.float16}} if self.fp16 else {}
        self._model = CrossEncoder(model_name, device=self.device, max_length=config.RERANK_MAX_TOKENS, **kwargs)
        self._lock = threading.Lock()  # one search at a time on the GPU

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        with self._lock:
            values = self._model.predict([(query, text) for text in texts], batch_size=32, show_progress_bar=False)
        return [float(v) for v in values]


_loaded: Reranker | None = None
_load_failed = False
_load_lock = threading.Lock()


def load_reranker() -> Reranker | None:
    """The reranker, loaded once; None (with one log line) if its packages or model are unavailable."""
    global _loaded, _load_failed
    with _load_lock:
        if _loaded is None and not _load_failed:
            try:
                _loaded = Reranker()
            except ImportError as e:
                _load_failed = True
                log.warning("reranking is off: %s is not installed (run `%s`); results are not reranked", e.name, INSTALL_HINT)
            except Exception as e:  # no model download possible, GPU out of memory, ...
                _load_failed = True
                log.warning("reranking is off: could not load %s (%s); results are not reranked", config.RERANK_MODEL, e)
        return _loaded


def rerank_order(ranked_ids: Sequence[str], scores: Sequence[float], top_n: int | None = None) -> list[str]:
    """`ranked_ids` with its first `top_n` re-ordered by `scores` (high first, ties by id); the rest unchanged.

    `scores` holds one score per id of that head, in the same order.
    """
    head = list(ranked_ids[: len(ranked_ids) if top_n is None else top_n])
    if len(scores) != len(head):
        raise ValueError(f"{len(scores)} scores for {len(head)} candidates")
    order = sorted(range(len(head)), key=lambda i: (-scores[i], head[i]))
    return [head[i] for i in order] + list(ranked_ids[len(head):])


def rerank_relevance(
    relevance: dict[str, float],
    query: str,
    text_of: Callable[[str], str],
    scorer: Scorer | None,
    top_n: int | None = None,
) -> dict[str, float]:
    """The pipeline's {job_id: relevance} with its best `top_n` jobs re-ordered by the reranker.

    The relevance *values* stay the same set of numbers: the job the reranker likes best gets
    the highest of them, and so on. So the rest of the score (ranking.rank) is not affected
    by the reranker's own scale. With `scorer=None` (reranker unavailable) nothing changes.
    """
    if scorer is None or not relevance:
        return relevance
    top_n = config.RERANK_TOP_N if top_n is None else top_n
    order = sorted(relevance, key=lambda d: (-relevance[d], d))
    head = [d for d in order[:top_n] if relevance[d] > 0]
    if len(head) < 2:
        return relevance
    reordered = rerank_order(head, scorer(query, [text_of(d) for d in head]))
    out = dict(relevance)
    for job_id, value in zip(reordered, sorted((relevance[d] for d in head), reverse=True)):
        out[job_id] = value
    return out
