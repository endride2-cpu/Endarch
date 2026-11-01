import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from qa_gate import check_chapter

class NameAliasTests(unittest.TestCase):
    def test_approved_translation_is_accepted_but_missing_or_partial_is_blocked(self):
        args = SimpleNamespace(min_ratio=.1, max_ratio=5, allow_terms=[], allow_flags=False)
        source = 'Third Imperial Prince berjalan ke istana.'
        target = 'Pangeran Ketiga Kekaisaran berjalan ke istana.'
        base = {'names': ['Third Imperial Prince']}
        status, issues = check_chapter(source, target, args, base)
        self.assertEqual(status, 'GAGAL')
        context = dict(base, name_aliases={'Third Imperial Prince': ['Pangeran Ketiga Kekaisaran']})
        self.assertEqual(check_chapter(source, target, args, context)[0], 'LULUS')
        self.assertEqual(check_chapter(source, 'Pangeran Ketiga berjalan ke istana.', args, context)[0], 'GAGAL')
        self.assertEqual(check_chapter(source, target + ' [AMBIGU: x]', args, context)[0], 'GAGAL')
