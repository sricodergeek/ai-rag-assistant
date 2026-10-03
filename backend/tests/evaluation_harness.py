"""Evaluation-only isolation; live services require RUN_LIVE_RAG_EVALUATIONS=1."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


def require_live_evaluation():
    if os.getenv("RUN_LIVE_RAG_EVALUATIONS") != "1":
        pytest.skip("Live RAG evaluation requires RUN_LIVE_RAG_EVALUATIONS=1 (OpenAI calls).")


def load_evaluation_vector_store():
    """Load the actual retrieval code independently, without opening persistent Chroma.

    Only the import-time client is stubbed. Live evaluations replace its collection
    with real ephemeral Chroma; offline regression tests supply mocked results.
    The application module and its global collection are never replaced.
    """
    import chromadb

    path = Path(__file__).resolve().parents[1] / "app" / "vector_store.py"
    spec = importlib.util.spec_from_file_location("evaluation_vector_store", path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(chromadb, "PersistentClient", return_value=Mock()):
        spec.loader.exec_module(module)
    return module
