"""Checks for the simulation tool-database wiring.

LinuxCNC chdirs to the INI directory before spawning DB_PROGRAM, so a bare
``-m tool_db_backend`` can resolve a stale config-local module. The shipped
sims must call QtPyVCP's module explicitly and point the GUI at the same
database file.
"""

import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "linuxcnc" / "configs"
DB_SIMS = [
    CONFIGS / "sim.qtpyvcp" / "xyz.ini",
    CONFIGS / "sim.qtpyvcp" / "xyz-metric.ini",
    CONFIGS / "sim.robot_thor" / "sim-robot_thor.ini",
]


class ToolDatabaseConfigTest(unittest.TestCase):
    def _ini(self, path):
        parser = configparser.ConfigParser(
            strict=False, inline_comment_prefixes=("#", ";"))
        parser.read(path)
        return parser

    def test_db_program_uses_qtpyvcp_module(self):
        for path in DB_SIMS:
            with self.subTest(path=path.name):
                parser = self._ini(path)
                program = parser.get("EMCIO", "DB_PROGRAM")
                self.assertIn("qtpyvcp.tools.tool_db_backend", program)
                self.assertNotRegex(program, r"-m\s+tool_db_backend\b")

    def test_tool_db_file_exists(self):
        for path in DB_SIMS:
            with self.subTest(path=path.name):
                parser = self._ini(path)
                db_file = parser.get("EMCIO", "TOOL_DB_FILE")
                self.assertTrue((path.parent / db_file).is_file(), db_file)


if __name__ == "__main__":
    unittest.main()
