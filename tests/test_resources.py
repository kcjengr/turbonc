"""Static checks for the Qt resource manifest."""

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QRC = ROOT / "src" / "tnc" / "resources.qrc"


class ResourceManifestTest(unittest.TestCase):
    def test_every_qrc_entry_exists(self):
        root = ET.parse(QRC).getroot()
        entries = [element.text for element in root.iter("file") if element.text]
        self.assertTrue(entries, "resources.qrc has no files")
        missing = [entry for entry in entries
                   if not (ROOT / "src" / "tnc" / entry).is_file()]
        self.assertEqual(missing, [], "resources.qrc references missing files")

    def test_no_duplicate_entries(self):
        root = ET.parse(QRC).getroot()
        entries = [element.text for element in root.iter("file") if element.text]
        self.assertEqual(len(entries), len(set(entries)))

    def test_rcc_builder_is_present(self):
        self.assertTrue((ROOT / "src" / "tnc" / "generate_resources.py").is_file())


if __name__ == "__main__":
    unittest.main()
