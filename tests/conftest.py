"""Shared test configuration."""

import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _collect_garbage_on_main_thread():
    """Free Qt objects from earlier tests on the main (GUI) thread.

    If a controller from a previous test were only collected later by a worker
    thread, its QTimers would be destroyed from the wrong thread (segfault)."""
    yield
    gc.collect()
