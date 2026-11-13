import shutil, subprocess, sys

import pytest

from details.settings import REPO_ROOT


def test_node_copy_transcript_tests():
    node = shutil.which("node")
    if not node:
        sys.__stderr__.write("\nSKIP: node not found (copy-transcript JS tests not run)\n")
        pytest.skip("node not found")
    r = subprocess.run([node, "--test", str(REPO_ROOT / "tests" / "js" / "copy-transcript.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-1800:] + r.stderr[-500:]
