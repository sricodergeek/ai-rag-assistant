# RAG evaluation workflow

The evaluation suite separates offline regression/scoring tests from opt-in live
evaluations. Run the commands below from the project root using the existing
backend virtual environment. They clear inherited live opt-in and pytest options,
disable automatic third-party pytest plugin loading, and avoid bytecode/cache writes.

## Offline tests

Run the harness safety tests:

```sh
env -u RUN_LIVE_RAG_EVALUATIONS -u PYTEST_ADDOPTS \
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  ./backend/.venv/bin/pytest backend/tests/test_evaluation_harness.py \
  -p no:cacheprovider -v
```

Run the mocked retrieval regression and offline PDF citation scoring tests:

```sh
env -u RUN_LIVE_RAG_EVALUATIONS -u PYTEST_ADDOPTS \
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  ./backend/.venv/bin/pytest backend/tests/test_retrieval_evaluation.py \
  backend/tests/test_pdf_citation_evaluation.py \
  -k 'not test_real' -p no:cacheprovider -v
```

These tests require neither OpenAI nor PostgreSQL. The retrieval harness loads the
unchanged retrieval module in an isolated namespace with a stub initialization
client, without opening application persistent Chroma. PDF scoring uses local
synthetic evidence and the existing Acme PDF fixture.

## Safe collection

Collection of these six protected evaluation modules does not initialize persistent
application Chroma or external-service clients:

```sh
env -u RUN_LIVE_RAG_EVALUATIONS -u PYTEST_ADDOPTS \
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  ./backend/.venv/bin/pytest --collect-only \
  backend/tests/test_retrieval_evaluation.py \
  backend/tests/test_real_retrieval_evaluation.py \
  backend/tests/test_answer_evaluation.py \
  backend/tests/test_citation_evaluation.py \
  backend/tests/test_unknown_answer_evaluation.py \
  backend/tests/test_pdf_citation_evaluation.py \
  -p no:cacheprovider -q
```

Permanent harness tests verify collection safety and default live-test skips in
fresh subprocesses. Those checks block service imports, network/database access,
and persistent Chroma file access. These guards belong to the regression checks;
they are not a general sandbox for arbitrary pytest execution.

## Live evaluation boundary

Live evaluations skip by default, before service imports. Only the exact setting
`RUN_LIVE_RAG_EVALUATIONS=1` permits them to proceed when selected for execution.
Use it intentionally: live retrieval generates real `text-embedding-3-small`
embeddings through OpenAI, and live answer/citation/refusal evaluations also call
the LLM. Collection alone does not run these evaluations, even with opt-in set.

Live evaluations retain real retrieval against temporary ephemeral Chroma
collections; they are not converted into mocked evaluations. They do not use the
application's persistent collection. Their service outputs and costs are not
reproduced by offline tests.

## Metrics and scope

`test_real_retrieval_evaluation.py` reports answer-bearing **Hit@1, Hit@3, and
Hit@5**: the fraction of its six questions with at least one retrieved chunk within
K containing the expected answer phrase. This is not conventional chunk-level
Recall@K. It uses character chunking of the Acme text fixture, whereas production
PDF ingestion chunks each page near word boundaries.

`test_retrieval_evaluation.py` supplies predetermined query results and checks
expected page presence. It is a retrieval-helper regression check, not a measured
retrieval-quality benchmark.

Component-level evaluations bypass `/ask`; they do not establish end-to-end API,
authorization, routing, persistence, or conversation-history quality. The small
synthetic Acme benchmark is a smoke benchmark and must not be presented as universal
production accuracy. Passing offline tests does not establish live benchmark scores.

## Older standalone scripts

Automated tests live under `backend/tests/`. The root `pytest.ini` sets
`norecursedirs = backend/app`, excluding that directory from normal recursive pytest
discovery. Ordinary automated test commands should target `backend/tests/` or
selected files within it, and should not include `backend/app/test_*.py`.

The exclusion exists because legacy standalone scripts can call external APIs,
initialize or modify persistent Chroma, and process local PDFs during import or
execution. They remain manual/diagnostic utilities; exclusion does not make them safe.

`norecursedirs` only controls recursive discovery. Explicitly targeting a standalone
script can bypass the exclusion and trigger its side effects, even during collection.

Live evaluations still require intentional `RUN_LIVE_RAG_EVALUATIONS=1` opt-in.
The discovery boundary is not a data-isolation guarantee for all of `backend/tests/`:
some other tests use application clients or PostgreSQL. For service-free evaluation,
use the focused offline commands above.

## Permanent discovery-boundary regression coverage

The root `pytest.ini` establishes the normal recursive discovery boundary.
`backend/tests/test_evaluation_harness.py` protects it with
`test_recursive_discovery_excludes_legacy_scripts`, which checks observable pytest
file collection: `backend/tests/test_*.py` files are discovered and `backend/app`
scripts are excluded. It does not merely check a configuration string.

The test runs in a fresh process using the existing import/service guards and inert
collectors, so application test modules and legacy scripts are not imported or
executed. A future configuration change that re-enables recursive discovery of
`backend/app` fails the guard before a legacy script is imported.

This protects discovery safety, not execution safety: explicitly targeting a legacy
script can still bypass `norecursedirs`, and other automated tests may use services
when executed. The separate live-evaluation guard requires
`RUN_LIVE_RAG_EVALUATIONS=1`; it does not govern legacy scripts.
