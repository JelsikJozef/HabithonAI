import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore


class TestRawDocument(unittest.TestCase):
    def test_size_invariant_and_ext_normalization(self):
        rd = RawDocument(path=Path('/tmp/a.pdf'), size=0, mtime=datetime(2024, 1, 1), ext='.PDF')
        self.assertEqual(rd.ext, 'pdf')
        with self.assertRaises(ValueError):
            RawDocument(path=Path('/tmp/b.txt'), size=-1, mtime=datetime(2024, 1, 1), ext='TXT')

    def test_as_dict_and_with_meta(self):
        rd = RawDocument(path=Path('docs/file.docx'), size=123, mtime=datetime(2023, 5, 4, 3, 2, 1), ext='docx')
        rd2 = rd.with_meta(tenant='acme', batch_id='42')
        self.assertEqual(rd.meta, {})
        self.assertEqual(rd2.meta['tenant'], 'acme')
        d = rd2.as_dict()
        self.assertEqual(d['ext'], 'docx')
        self.assertEqual(d['path'], 'docs/file.docx')
        self.assertIn('T', d['mtime'])  # ISO format includes 'T'


class TestParsedDocument(unittest.TestCase):
    def test_short_text_and_tokens_invariant(self):
        rd = RawDocument(path=Path('x'), size=1, mtime=datetime(2024, 1, 1), ext='txt')
        pd = ParsedDocument(text='  Hello World!  ', source=rd, language='sk', tokens=10)
        self.assertEqual(pd.short_text(5), 'Hello...')
        self.assertEqual(pd.short_text(0), '')
        with self.assertRaises(ValueError):
            ParsedDocument(text='x', source=rd, tokens=-5)

    def test_to_record_structure(self):
        rd = RawDocument(path=Path('a/b/c.pdf'), size=10, mtime=datetime(2024, 1, 1, 1, 2, 3), ext='pdf', meta={'t': 'X'})
        pd = ParsedDocument(text='text', source=rd, charset='utf-8', language='sk', hash='h', tokens=1, metadata={'pages': 3})
        rec = pd.to_record()
        self.assertEqual(rec['text'], 'text')
        self.assertEqual(rec['source']['ext'], 'pdf')
        self.assertEqual(rec['metadata']['pages'], 3)


if __name__ == '__main__':
    unittest.main()
