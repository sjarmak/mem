"""Keeps the test suite from writing build artefacts into the published tree.

Four test modules import ``public/validator/membench_validate.py`` by path, the way a
downloader runs it - ``test_public_validator_tampers``, ``test_public_validator_parity``,
``test_public_corpus_contract`` and ``public_figures``. Every one of them goes through
``importlib``'s source loader, which caches bytecode NEXT TO THE SOURCE. Running the
suite therefore created ``public/validator/__pycache__/membench_validate.cpython-312.pyc``
inside the release directory: not source, not in any digest, and shipped verbatim by
anyone who packaged ``public/`` as it sat on disk. It is gitignored, which is exactly
why nobody saw it.

Redirecting ``sys.pycache_prefix`` moves every bytecode file this process writes into a
cache directory outside the trees under test, for the published tree and the package
alike. It is set here, at import time of the package's conftest, because the imports it
governs happen while the tests RUN, long after this file is loaded. Turning caching off
instead would have worked and would have recompiled the whole suite on every run; the
redirect keeps the cache and moves it.

WHAT THIS FILE DOES NOT COVER, measured rather than assumed: a conftest is loaded by
pytest and by nothing else, so any process that imports these modules directly still
writes into the release. ``PYTHONPATH=. python -m tests.public_figures`` - the documented
recipe for re-deriving the published figures - puts the ``__pycache__`` back, and so does
any script that does ``import tests.test_public_corpus_contract``. Closing that needs the
redirect in ``tests/__init__.py``, which every one of those routes imports and this file
is not; moving it there is a cross-lane change, so it is handed off rather than made here.

The seal in ``membench.public_seal`` is the other half, and the load-bearing one: it
refuses to seal a tree carrying a file that is not published source, so an artefact
neither this file nor that move prevents still cannot ship. It is what caught the
artefact this file was written for. ``test_public_release_seal.py`` asserts both ends."""

from __future__ import annotations

import sys
from pathlib import Path

_PYCACHE = Path(__file__).resolve().parents[1] / ".pytest_cache" / "pycache"
_PYCACHE.mkdir(parents=True, exist_ok=True)
sys.pycache_prefix = str(_PYCACHE)
