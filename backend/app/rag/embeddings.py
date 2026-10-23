"""Lazy, CPU-only E5. No model download at import and one instance per process."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from threading import Lock

import numpy as np

MODEL = "intfloat/multilingual-e5-small"
DEFAULT_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
DIMENSIONS = 384


@dataclass(frozen=True)
class EmbeddingSettings:
    revision: str = DEFAULT_REVISION
    cache_dir: str | None = None
    local_files_only: bool = False
    batch_size: int = 8
    threads: int = 2

    @classmethod
    def from_env(cls):
        return cls(revision=os.getenv("RAG_EMBEDDING_REVISION", DEFAULT_REVISION),
                   cache_dir=os.getenv("RAG_MODEL_CACHE"),
                   local_files_only=os.getenv("RAG_LOCAL_FILES_ONLY", "0") == "1",
                   batch_size=int(os.getenv("RAG_BATCH_SIZE", "8")),
                   threads=int(os.getenv("RAG_CPU_THREADS", "2")))


def checked_vectors(values, count: int):
    array = np.asarray(values, dtype=np.float32)
    if array.shape != (count, DIMENSIONS) or not np.isfinite(array).all():
        raise ValueError("Embedding должен содержать 384 конечных числа на каждый текст")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if (norms < 1e-8).any():
        raise ValueError("Нулевой embedding недопустим")
    return array / norms


class E5Encoder:
    def __init__(self, settings: EmbeddingSettings):
        if not 1 <= settings.batch_size <= 64 or not 1 <= settings.threads <= 32:
            raise ValueError("Некорректные batch_size/threads")
        from huggingface_hub import snapshot_download
        from sentence_transformers import SentenceTransformer
        import torch
        torch.set_num_threads(settings.threads)
        path = Path(snapshot_download(MODEL, revision=settings.revision, cache_dir=settings.cache_dir,
            local_files_only=settings.local_files_only,
            allow_patterns=["*.json", "*.txt", "*.model", "*.safetensors"],
            ignore_patterns=["onnx/*", "openvino/*"]))
        self.model = SentenceTransformer(str(path), device="cpu", local_files_only=True, trust_remote_code=False)
        self.model.max_seq_length = 512
        self.settings = settings
        self.lock = Lock()  # Bound concurrent CPU work and temporary activation memory.
        self.identity = f"{MODEL}@{path.name}:mean:normalized:384:prefix-v1"

    def split_passage(self, text: str) -> list[str]:
        encoded = self.model.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True,
                                       truncation=False, verbose=False)
        offsets = encoded["offset_mapping"]
        if not offsets:
            return []
        if len(offsets) <= 480:
            return [text]  # Preserve exact numbers/punctuation; do not decode/rewrite short excerpts.
        # Room for E5 prefix and special tokens. Long texts must never truncate silently.
        return [text[offsets[i][0]:offsets[min(i + 480, len(offsets)) - 1][1]]
                for i in range(0, len(offsets), 440)]

    def _encode(self, texts: list[str], prefix: str):
        if not texts:
            return np.empty((0, DIMENSIONS), dtype=np.float32)
        inputs = [prefix + text for text in texts]
        if any(not text.strip() for text in texts):
            raise ValueError("Нельзя векторизовать пустой текст")
        if any(len(self.model.tokenizer.encode(text, verbose=False)) > 512 for text in inputs):
            raise ValueError("Текст превышает 512 токенов; используйте split_passage")
        with self.lock:
            vectors = self.model.encode(inputs, batch_size=self.settings.batch_size,
                normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        return checked_vectors(vectors, len(texts))

    def passages(self, texts: list[str]):
        return self._encode(texts, "passage: ")

    def query(self, text: str):
        return self._encode([text], "query: ")[0]


_encoder = None
_encoder_lock = Lock()


def get_encoder() -> E5Encoder:
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            _encoder = E5Encoder(EmbeddingSettings.from_env())
        return _encoder


def fingerprint(text: str, model_key: str) -> str:
    return hashlib.sha256(json.dumps([text, model_key], ensure_ascii=False).encode()).hexdigest()
