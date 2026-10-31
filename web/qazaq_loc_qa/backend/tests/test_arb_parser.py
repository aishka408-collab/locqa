import unittest
import os
from pathlib import Path

from qazaq_loc_qa.backend.utils.arb_parser import ARBFile, ARBEntry

class TestARBParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # create sample ARB content
        cls.sample_path = Path(__file__).parent / "data" / "sample.arb"
        os.makedirs(cls.sample_path.parent, exist_ok=True)
        cls.sample_path.write_text(
            '{\n'
            '    "@@comment1": "This is a comment",\n'
            '    "welcome": "Сәлеметсіз бе",\n'
            '    "@invalid": "Should be treated as comment",\n'
            '    "@#AnotherComment": "Another comment",\n'
            '    "same_as_source": "same_as_source",\n'
            '    "untranslated": "untranslated",\n'
            '    "placeholder": "Hello {name}",\n'
            '    "plural": "{count, plural, one{One} other{Many}}",\n'
            '}\n'
        , encoding='utf-8')
        cls.arb = ARBFile.load(cls.sample_path)

    def test_load_counts(self):
        # Include the opening and closing braces so rewritten ARB remains valid JSON.
        self.assertEqual(len(self.arb.entries), 10)
        self.assertEqual(len(self.arb), 6)  # 6 kv entries
        self.assertIn('welcome', self.arb)
        self.assertIn('placeholder', self.arb)

    def test_get_and_set(self):
        original = self.arb.get('welcome')
        self.assertEqual(original, "Сәлеметсіз бе")
        self.arb.set('welcome', 'Қайырлы күн')
        self.assertEqual(self.arb.get('welcome'), 'Қайырлы күн')
        # verify that dump preserves order and formatting
        tmp_path = Path(self.sample_path).with_suffix('.out.arb')
        self.arb.dump(tmp_path)
        # reload and compare
        reloaded = ARBFile.load(tmp_path)
        self.assertEqual(reloaded.get('welcome'), 'Қайырлы күн')
        # ensure comment lines are still present
        comment_keys = [e.key for e in reloaded.entries if e.type == 'comment']
        self.assertIn('@@comment1', comment_keys)
        # cleanup
        tmp_path.unlink()

    def test_duplicate_key_set_updates_every_occurrence(self):
        import tempfile
        path = Path(tempfile.mkdtemp()) / "duplicate.arb"
        path.write_text(
            '{\n'
            '    "cancel": "Болдырмау",\n'
            '    "other": "Басқа",\n'
            '    "cancel": "БАСТАУ"\n'
            '}\n',
            encoding="utf-8",
        )
        arb = ARBFile.load(path)
        self.assertEqual(arb.get("cancel"), "БАСТАУ")  # last occurrence wins
        arb.set("cancel", "Бас тарту")
        self.assertEqual(arb.get("cancel"), "Бас тарту")
        out = path.with_suffix(".out.arb")
        arb.dump(out)
        self.assertEqual(ARBFile.load(out).get("cancel"), "Бас тарту")
        occurrences = [e for e in ARBFile.load(out).entries if e.type == "kv" and e.key == "cancel"]
        self.assertEqual([e.value for e in occurrences], ["Бас тарту", "Бас тарту"])
        out.unlink()

    def test_comment_parsing(self):
        comment_entries = [e for e in self.arb.entries if e.type == 'comment']
        self.assertGreaterEqual(len(comment_entries), 1)
        self.assertEqual(comment_entries[0].key, '@@comment1')
        self.assertEqual(comment_entries[0].value, 'This is a comment')

    def test_non_comment_at_prefix(self):
        # Keys starting with single @ should be treated as kv, not comment
        self.assertIn('@invalid', self.arb)
        self.assertEqual(self.arb.get('@invalid'), 'Should be treated as comment')

if __name__ == '__main__':
    unittest.main()
