"""Fresh-agent credential isolation and offline stage regression tests."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import config  # noqa: E402
import pipeline_unified as pu  # noqa: E402


class FreshAgentTests(unittest.TestCase):
    def test_no_old_key_fallback(self):
        with mock.patch.dict(os.environ, {'OPENCODE_GO_API_KEY': 'fake-old'}, clear=True):
            self.assertIsNone(config.load_api_key())
            self.assertEqual(config.api_base(), '')

    def test_custom_pair(self):
        with mock.patch.dict(os.environ, {'NOVEL_API_KEY': 'fake-new',
                                         'NOVEL_API_BASE': 'https://example.invalid/v1'}, clear=True):
            self.assertEqual(config.load_api_key(), 'fake-new')
            self.assertEqual(config.api_base(), 'https://example.invalid/v1')

    def test_explicit_provider_isolation(self):
        with mock.patch.dict(os.environ, {'NOVEL_API_KEY': 'fake-custom',
                                         'OPENCODE_GO_API_KEY': 'fake-go'}, clear=True):
            self.assertIsNone(config.load_api_key('zen'))
            self.assertEqual(config.load_api_key('go'), 'fake-go')

    def test_no_legacy_channels(self):
        self.assertEqual(json.loads((ROOT / 'config/channels.json').read_text()), {})

    def test_disabled_refine_needs_no_client(self):
        with mock.patch.object(pu, 'make_client', side_effect=AssertionError('must stay offline')):
            pu.run_stage('refine', {'refine': False})


if __name__ == '__main__':
    unittest.main()