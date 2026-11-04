"""Click-to-seek helpers in web/analyse/api.js, run with `node --test` (skipped, visibly, when node is missing)."""
import shutil, subprocess, sys

import pytest

from details.settings import REPO_ROOT


def test_node_unit_tests():
    node = shutil.which("node")
    if not node:
        sys.__stderr__.write("\nSKIP: node not found (analyse JS helper tests not run)\n")
        pytest.skip("node not found")
    r = subprocess.run([node, "--test", str(REPO_ROOT / "tests" / "js" / "analyse.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
