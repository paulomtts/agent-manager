"""Rule 1 for the modules that live below pygents (card 7a744199).

`runtime/walk.py` and `runtime/errors.py` sit inside `runtime/` -- the one
package allowed to import pygents -- but `dispatch.py`, `prompt.py` and
`results.py` import them, and none of those may load pygents. Checked in a
fresh interpreter, because this process has pygents loaded already (the
runtime conftest imports it), and a static scan of one file would miss a
transitive import through `runtime/__init__.py`.
"""

import subprocess
import sys

import pytest

PYGENTS_FREE = (
    "agent_manager.runtime",
    "agent_manager.runtime.errors",
    "agent_manager.runtime.walk",
    "agent_manager.dispatch",
    "agent_manager.prompt",
    "agent_manager.results",
    "agent_manager.workflow.phases",
)


@pytest.mark.parametrize("module", PYGENTS_FREE)
def test_importing_the_module_loads_no_pygents(module):
    probe = (
        "import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        "loaded = sorted(m for m in sys.modules if m == 'pygents' or m.startswith('pygents.'))\n"
        "print(','.join(loaded))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "", f"{module} loaded {done.stdout.strip()}"
