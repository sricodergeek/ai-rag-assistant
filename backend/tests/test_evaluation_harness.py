import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from backend.tests.evaluation_harness import (
    load_evaluation_vector_store,
    require_live_evaluation,
)


def test_live_evaluation_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("RUN_LIVE_RAG_EVALUATIONS", raising=False)
    with pytest.raises(pytest.skip.Exception, match="RUN_LIVE_RAG_EVALUATIONS=1"):
        require_live_evaluation()
    monkeypatch.setenv("RUN_LIVE_RAG_EVALUATIONS", "0")
    with pytest.raises(pytest.skip.Exception):
        require_live_evaluation()


def test_evaluation_retrieval_namespaces_are_isolated():
    original = sys.modules.get("backend.app.vector_store")
    first = load_evaluation_vector_store()
    second = load_evaluation_vector_store()
    first.collection.query.return_value = {
        "documents": [["Evidence"]],
        "metadatas": [[{"source": "fixture.pdf", "page": 1}]],
        "distances": [[0.25]],
    }
    assert first.search_documents([0.0], top_k=5, document_id="fixture") == [
        {"text": "Evidence", "source": "fixture.pdf", "page": 1, "distance": 0.25}
    ]
    first.collection.query.assert_called_once_with(
        query_embeddings=[[0.0]], n_results=5, where={"document_id": "fixture"},
        include=["documents", "metadatas", "distances"],
    )
    second.collection.query.assert_not_called()
    assert sys.modules.get("backend.app.vector_store") is original


def _run_guarded_evaluation_pytest(*options, recursive_discovery=False):
    """Use the same fresh-process safety guards for collection and default skips."""
    project_root = Path(__file__).resolve().parents[2]
    files = [
        "backend/tests/test_retrieval_evaluation.py",
        "backend/tests/test_real_retrieval_evaluation.py",
        "backend/tests/test_answer_evaluation.py",
        "backend/tests/test_citation_evaluation.py",
        "backend/tests/test_unknown_answer_evaluation.py",
        "backend/tests/test_pdf_citation_evaluation.py",
    ]
    script = textwrap.dedent("""
        import importlib.abc
        import os
        from pathlib import Path
        import sys
        import pytest

        attempts = []
        persistent_path = Path('backend/chroma_db').resolve()
        recursive_discovery = '--discovery-probe' in sys.argv
        arguments = [arg for arg in sys.argv[1:] if arg != '--discovery-probe']
        tests_path = Path('backend/tests').resolve()
        app_path = Path('backend/app').resolve()

        def deny(description):
            attempts.append(description)
            raise AssertionError(description)

        class ServiceImportGuard(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith('backend.app.test_') or fullname in {
                    'chromadb', 'openai', 'redis', 'psycopg',
                    'backend.app.vector_store', 'backend.app.database',
                    'backend.app.embeddings', 'backend.app.llm',
                }:
                    deny('Service initialization import: ' + fullname)

        def audit(event, args):
            if event in {'socket.connect', 'socket.getaddrinfo', 'sqlite3.connect'}:
                deny('External or database access: ' + event)
            if event == 'open' and isinstance(args[0], (str, bytes)):
                path = Path(args[0].decode() if isinstance(args[0], bytes) else args[0]).resolve()
                if path == persistent_path or persistent_path in path.parents:
                    deny('Persistent Chroma file access: ' + str(path))

        sys.meta_path.insert(0, ServiceImportGuard())
        sys.addaudithook(audit)
        assert 'RUN_LIVE_RAG_EVALUATIONS' not in os.environ

        class DiscoveryProbe(pytest.File):
            def collect(self):
                # Verify file discovery without importing application test modules.
                return ()

        class Outcomes:
            def __init__(self):
                self.selected = set()
                self.skipped = set()
                self.calls = []
                self.discovered_files = set()

            def pytest_pycollect_makemodule(self, module_path, parent):
                if recursive_discovery:
                    path = module_path.resolve()
                    if app_path in path.parents:
                        deny('Legacy script selected for collection: ' + str(path))
                    assert tests_path in path.parents, path
                    self.discovered_files.add(path)
                    return DiscoveryProbe.from_parent(parent, path=module_path)

            def pytest_collection_finish(self, session):
                self.selected = {item.nodeid for item in session.items}

            def pytest_runtest_logreport(self, report):
                if report.when in {'setup', 'call'} and report.skipped:
                    self.skipped.add(report.nodeid)
                if report.when == 'call' and not report.skipped:
                    self.calls.append(report.nodeid)

        outcomes = Outcomes()
        result = pytest.main(
            ['-q', '-p', 'no:cacheprovider', *arguments], plugins=[outcomes]
        )
        assert not attempts, attempts
        if recursive_discovery:
            expected = set(tests_path.rglob('test_*.py'))
            assert expected and outcomes.discovered_files == expected, outcomes.discovered_files
            assert not any(name.startswith('backend.app.test_') for name in sys.modules)
            assert result == pytest.ExitCode.NO_TESTS_COLLECTED, result
            print('Discovery boundary verified: backend/tests modules found; no legacy imports.')
            result = 0  # Probes intentionally collect no executable test items.
        if '--collect-only' not in sys.argv:
            assert len(outcomes.selected) == 5, outcomes.selected
            assert outcomes.skipped == outcomes.selected, outcomes.skipped
            assert not outcomes.calls, outcomes.calls
        raise SystemExit(result)
    """)
    environment = os.environ.copy()
    environment.pop("RUN_LIVE_RAG_EVALUATIONS", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment.pop("PYTEST_ADDOPTS", None)
    targets = ["--discovery-probe"] if recursive_discovery else files
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, *options, *targets],
        cwd=project_root, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_evaluation_modules_are_safe_to_collect():
    output = _run_guarded_evaluation_pytest("--collect-only")
    files = [
        "test_retrieval_evaluation.py", "test_real_retrieval_evaluation.py",
        "test_answer_evaluation.py", "test_citation_evaluation.py",
        "test_unknown_answer_evaluation.py", "test_pdf_citation_evaluation.py",
    ]
    for file in files:
        assert file + "::" in output, output


def test_live_evaluations_skip_without_initializing_services():
    output = _run_guarded_evaluation_pytest("-k", "test_real")
    assert "5 skipped" in output, output


def test_recursive_discovery_excludes_legacy_scripts():
    output = _run_guarded_evaluation_pytest("--collect-only", recursive_discovery=True)
    assert "Discovery boundary verified" in output, output
