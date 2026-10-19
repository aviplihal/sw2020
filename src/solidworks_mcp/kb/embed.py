"""Vectors for the drawings and descriptions, from Voyage AI.

Nothing goes to Voyage until SWMCP_VOYAGE_OPTED_OUT=1 says the account has
opted out of Voyage storing and training on its data (Docs/KNOWLEDGE-BASE.md
§5.4). Without it, or when Voyage cannot be reached, a case is saved and
marked 'embedding pending'; kb_reembed_pending catches up later.
"""

import os

import numpy as np

IMAGE_MODEL = "voyage-multimodal-3.5"
TEXT_MODEL = "voyage-4"


class EmbeddingUnavailable(Exception):
    """Embedding was not possible now; the case is kept and marked pending."""


class Embedder:
    """Turns images and texts into unit vectors. Subclassed by VoyageEmbedder and by test fakes."""

    image_model = IMAGE_MODEL
    text_model = TEXT_MODEL

    def images(self, images: list, query: bool = False) -> list:
        raise NotImplementedError

    def texts(self, texts: list, query: bool = False) -> list:
        raise NotImplementedError


class VoyageEmbedder(Embedder):
    def __init__(self) -> None:
        self._client = None

    def _voyage(self):
        if os.environ.get("SWMCP_VOYAGE_OPTED_OUT") != "1":
            raise EmbeddingUnavailable(
                "nothing was sent to Voyage: set SWMCP_VOYAGE_OPTED_OUT=1 once the Voyage account has opted out "
                "of data use (dashboard: Organization > Terms of Service), restart the server, then run "
                "kb_reembed_pending")
        if not os.environ.get("VOYAGE_API_KEY"):
            raise EmbeddingUnavailable("VOYAGE_API_KEY is not set")
        if self._client is None:
            try:
                import voyageai
            except ImportError:
                raise EmbeddingUnavailable('voyageai is not installed: pip install "solidworks-mcp[kb]"') from None
            self._client = voyageai.Client()  # reads VOYAGE_API_KEY
        return self._client

    def images(self, images: list, query: bool = False) -> list:
        client = self._voyage()
        try:
            result = client.multimodal_embed(inputs=[[image] for image in images], model=self.image_model,
                                             input_type="query" if query else "document")
        except Exception as exc:  # noqa: BLE001 - any failure leaves the case pending, never lost
            raise EmbeddingUnavailable(f"Voyage failed: {type(exc).__name__}: {exc}") from exc
        return [unit(v) for v in result.embeddings]

    def texts(self, texts: list, query: bool = False) -> list:
        client = self._voyage()
        try:
            result = client.embed(texts, model=self.text_model, input_type="query" if query else "document")
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingUnavailable(f"Voyage failed: {type(exc).__name__}: {exc}") from exc
        return [unit(v) for v in result.embeddings]


def unit(vector) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    return v / norm if norm else v
