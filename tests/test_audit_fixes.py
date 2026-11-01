"""Regresi untuk perbaikan audit."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from typo import normalize_typo  # noqa: E402
import pipeline_unified as pu  # noqa: E402


class TypoProtectTests(unittest.TestCase):
    def test_img_token_untouched(self):
        self.assertEqual(normalize_typo("[IMG:a.jpg]"), "[IMG:a.jpg]")
        self.assertEqual(normalize_typo("Lihat ini,ya [IMG:b c.png] oke...!"),
                         "Lihat ini, ya [IMG:b c.png] oke…!")

    def test_url_untouched(self):
        self.assertEqual(normalize_typo("buka http://x.com/a?b=1,2 sekarang"),
                         "buka http://x.com/a?b=1,2 sekarang")


class RawMapTests(unittest.TestCase):
    def test_identity_default(self):
        self.assertEqual(pu.resolve_raw_map(None, 3, 3), {1: [1], 2: [2], 3: [3]})

    def test_merge_shift_and_none(self):
        m = pu.resolve_raw_map({"2": [2, 3], "3": 4, "1": None}, 3, 4)
        self.assertEqual(m, {2: [2, 3], 3: [4]})

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            pu.resolve_raw_map({"2": 9}, 3, 4)


class ChatRetryTests(unittest.TestCase):
    def test_empty_response_is_retried(self):
        def resp(content):
            msg = mock.Mock(content=content)
            return mock.Mock(choices=[mock.Mock(message=msg, finish_reason="stop")], usage=None)
        client = mock.Mock()
        client.base_url = "https://example.invalid/v1"
        client.chat.completions.create.side_effect = [resp(""), resp("hasil")]
        with mock.patch.object(pu.time, "sleep"):
            self.assertEqual(pu.chat(client, "m", "halo", 10), "hasil")
        self.assertEqual(client.chat.completions.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
