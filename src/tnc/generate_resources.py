#!/usr/bin/env python3
"""Compile the Qt resource bundle used by TurboNC.

``src/tnc/resources.qrc`` is compiled into the binary ``src/tnc/tnc.rcc``,
which ``tnc.main`` registers at startup with ``QResource.registerResource``.

This replaces the old ``resources_rc.py`` (a 19 MB generated Python module).
That file was cheap to *compile* but expensive to *import*: it starts with
``from PySide6 import QtCore``, and shiboken6's import hook
(``shibokensupport/feature.py:_mod_uses_pyside``) reacts by running
``inspect.getsource()`` over the whole module to search for the string
"PySide6". ``linecache`` then retains every one of its ~418k lines
(~40 MB RSS) for the lifetime of the process.

Run after editing ``resources.qrc`` or the files under ``images/``::

    python src/tnc/generate_resources.py

Requires ``pyside6-rcc`` (shipped with PySide6).
"""

import os
import shutil
import subprocess
import sys

TNC_DIR = os.path.dirname(os.path.abspath(__file__))
QRC_FILE = os.path.join(TNC_DIR, "resources.qrc")
RCC_FILE = os.path.join(TNC_DIR, "tnc.rcc")


def find_rcc():
    """Return the path to the PySide6 resource compiler."""
    override = os.environ.get("PYSIDE6_RCC")
    if override:
        return override

    # Prefer the rcc belonging to the running PySide6 installation.
    try:
        import PySide6

        candidate = os.path.join(os.path.dirname(PySide6.__file__),
                                 "pyside6-rcc")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    except ImportError:
        pass

    for name in ("pyside6-rcc", "pyside6-rcc6"):
        found = shutil.which(name)
        if found:
            return found
    return None


def main():
    if not os.path.isfile(QRC_FILE):
        sys.exit("error: %s not found" % QRC_FILE)

    rcc = find_rcc()
    if rcc is None:
        sys.exit("error: pyside6-rcc not found (install PySide6 or set "
                 "PYSIDE6_RCC)")

    print("Compiling %s => %s" % (os.path.basename(QRC_FILE),
                                  os.path.basename(RCC_FILE)))
    result = subprocess.run([rcc, "--binary", QRC_FILE, "-o", RCC_FILE])
    if result.returncode != 0:
        sys.exit("error: pyside6-rcc failed with exit code %d"
                 % result.returncode)

    print("Wrote %s (%.1f MB)" % (RCC_FILE,
                                  os.path.getsize(RCC_FILE) / 1024 / 1024))


if __name__ == "__main__":
    main()
