"""Pytest configuration : DB temporaire isolee par session de tests."""
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


@pytest.fixture(scope="session", autouse=True)
def isolated_data_dir():
    """Place DATA_DIR dans un temp dir avant l'import de l'app."""
    tmp = tempfile.mkdtemp(prefix="aubepilot-test-")
    os.environ["AUBEDRONISTE_DATA"] = tmp
    yield tmp
    # Pas de cleanup : pytest peut vouloir relire les eml dumps
