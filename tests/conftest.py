"""Shared pytest fixtures.

pipeline.state_store uses a single module-level DB_PATH rather than an
injectable path. Rather than reload the module (which would break identity for
anything that already did `from pipeline import state_store as store`, e.g.
pipeline.orchestrator), we just repoint the existing module's DB_PATH
attribute at a fresh temp file per test — every consumer shares the same
module object either way, so this is both simpler and safer.
"""

import pytest

from pipeline import state_store as store


@pytest.fixture
def state_store(tmp_path):
    store.DB_PATH = tmp_path / "test_state.db"
    store.init_db()
    return store
