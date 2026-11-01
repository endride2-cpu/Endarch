import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from typo import normalize_typo


class TypoTests(unittest.TestCase):
    def test_dialogue(self):
        cases = {
            '"Kenapa?" tanyanya.': '"Kenapa?" tanyanya.',
            '“Ayo pergi,” katanya.': '“Ayo pergi,” katanya.',
            '"Apa?!"': '"Apa?!"',
            '"Tidak!!"': '"Tidak!!"',
            '"Tapi—"': '"Tapi—"',
            '"Ayo,"katanya': '"Ayo," katanya',
            '「Pergi!」 katanya': '「Pergi!」 katanya',
            '『Apa?』 jawabnya': '『Apa?』 jawabnya',
            "'Siapa?' katanya": "'Siapa?' katanya",
            '“Tunggu…” katanya': '“Tunggu…” katanya',
            '“Jangan?!”, katanya': '“Jangan?!”, katanya',
            '"Ya!!!"': '"Ya!!!"',
            '"Serius??"': '"Serius??"',
            'Tapi—dia pergi.': 'Tapi—dia pergi.',
            'Halo...': 'Halo…',
            'Apa?Dia': 'Apa? Dia',
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_typo(source), expected)


if __name__ == "__main__":
    unittest.main()
