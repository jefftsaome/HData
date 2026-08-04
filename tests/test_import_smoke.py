import subprocess
import sys


def _fresh(expr: str) -> int:
    return subprocess.run([sys.executable, "-c", expr], capture_output=True).returncode


def test_import_in_leaf_first_order():
    assert _fresh("import hdata.client, hdata.sources") == 0


def test_import_in_source_first_order():
    assert _fresh("import hdata.sources, hdata.client") == 0


def test_import_auth_internals_direct():
    assert _fresh("import hdata.auth.session, hdata.auth.token_manager") == 0
