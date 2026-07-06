r"""pytest bootstrap.

Two constraints shape where temp dirs go:

* C: is full, so temp files must land on D:.
* The permission layer (``mycode.permissions.check_read_path``) only allows
  paths inside the project root, and the tool tests exercise real tools against
  ``tmp_path``. So ``tmp_path`` must live *under the project root*.

Pointing pytest's temp root at ``<root>/.pytmp/run-<pid>`` via
PYTEST_DEBUG_TEMPROOT satisfies both and avoids reusing stale Windows temp roots
that may have broken ACLs or junctions after a crashed run.
"""

import os
from pathlib import Path

# NB: use a per-process root. Reusing pytest's own ``pytest-of-USER`` directory
# can get stuck on Windows if a previous crashed run left broken ACLs or
# long-path junctions behind.
_TMP_ROOT = Path(__file__).resolve().parent / ".pytmp" / f"run-{os.getpid()}"
_TMP_ROOT.mkdir(exist_ok=True)
os.environ["PYTEST_DEBUG_TEMPROOT"] = str(_TMP_ROOT)
