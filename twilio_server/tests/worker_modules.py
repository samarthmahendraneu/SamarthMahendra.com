"""Load modules that run on the pythonserver worker, which lives in its own folder.

Its own helpers come from that folder too. The shared modules (events, jobs,
callbacks, question_store) resolve to twilio_server's copies, which the drift
test in test_questions.py keeps identical.
"""

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
FOLDER = REPO / "pythonserver"


def load(name):
    if str(FOLDER) not in sys.path:
        sys.path.append(str(FOLDER))
    spec = importlib.util.spec_from_file_location(name, FOLDER / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
