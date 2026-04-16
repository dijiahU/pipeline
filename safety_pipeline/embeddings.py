import logging
import warnings

from .settings import LOCAL_EMBEDDING_MODEL


_local_embedding_model = None


def get_local_embedding_model():
    global _local_embedding_model
    if _local_embedding_model is not None:
        return _local_embedding_model
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for embedding retrieval. "
            "Install with: pip install sentence-transformers"
        ) from exc

    print(f"[embeddings] loading local embedding model: {LOCAL_EMBEDDING_MODEL}")
    logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*position_ids.*")
            warnings.filterwarnings("ignore", message=".*unauthenticated.*")
            _local_embedding_model = SentenceTransformer(LOCAL_EMBEDDING_MODEL)
    except Exception as exc:
        raise RuntimeError(
            f"failed to load local embedding model '{LOCAL_EMBEDDING_MODEL}': "
            f"{exc.__class__.__name__}: {exc}"
        ) from exc
    return _local_embedding_model
