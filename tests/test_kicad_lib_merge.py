import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kicad_lib_merge", ROOT / "tools" / "kicad_lib_merge.py")
MERGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MERGE)


def symbol(name, value):
    return (f'\t(symbol "{name}"\n'
            f'\t\t(property "Value" "{value}")\n'
            f'\t\t(symbol "{name}_1_1" (pin_names (offset 0)))\n'
            f'\t)')


def library(*blocks, final_newline=True):
    text = ('(kicad_symbol_lib\n'
            '\t(version 20231120)\n'
            '\t(generator_version "8.0")\n')
    if blocks:
        text += '\n'.join(blocks) + '\n'
    text += ')'
    return text + ('\n' if final_newline else '')


class SymbolMergeTests(unittest.TestCase):
    def test_quote_aware_top_level_parser(self):
        quoted = symbol(r'A\"(B)', 'text with ) and ( and \\"quote\\"')
        blocks, names = MERGE.split_tops(library(quoted))
        self.assertEqual(names, ['A"(B)'])
        self.assertEqual(blocks['A"(B)'], quoted)

    def test_unilateral_edits_and_matching_edits(self):
        base = library(symbol('A', 'old'), symbol('B', 'old'))
        ours = library(symbol('A', 'ours'), symbol('B', 'old'))
        theirs = library(symbol('A', 'old'), symbol('B', 'theirs'))
        merged = MERGE.merge_texts(base, ours, theirs)
        blocks, _ = MERGE.split_tops(merged)
        self.assertIn('"ours"', blocks['A'])
        self.assertIn('"theirs"', blocks['B'])

        same = library(symbol('A', 'same'), symbol('B', 'old'))
        merged = MERGE.merge_texts(base, same, same)
        self.assertIn('"same"', MERGE.split_tops(merged)[0]['A'])

    def test_unilateral_library_header_change_is_kept(self):
        base = library(symbol('A', 'old'))
        theirs = base.replace('(version 20231120)', '(version 20241209)')
        merged = MERGE.merge_texts(base, base, theirs)
        self.assertIn('(version 20241209)', merged)

    def test_unilateral_delete_wins(self):
        base = library(symbol('A', 'old'), symbol('B', 'old'))
        ours = library(symbol('B', 'old'))
        merged = MERGE.merge_texts(base, ours, base)
        self.assertEqual(MERGE.top_names(merged), ['B'])

    def test_conflicting_edit_add_and_delete_abort(self):
        base = library(symbol('A', 'old'))
        cases = (
            (library(symbol('A', 'ours')), library(symbol('A', 'theirs'))),
            (library(symbol('A', 'old'), symbol('B', 'ours')),
             library(symbol('A', 'old'), symbol('B', 'theirs'))),
            (library(), library(symbol('A', 'theirs'))),
        )
        bases = (base, base, base)
        for merge_base, (ours, theirs) in zip(bases, cases):
            with self.subTest(ours=ours, theirs=theirs):
                with self.assertRaisesRegex(MERGE.MergeError, 'conflict'):
                    MERGE.merge_texts(merge_base, ours, theirs)

    def test_preference_applies_only_to_conflicts(self):
        base = library(symbol('A', 'old'), symbol('B', 'old'))
        ours = library(symbol('A', 'ours'), symbol('B', 'old'))
        theirs = library(symbol('A', 'theirs'), symbol('B', 'new'))
        merged = MERGE.merge_texts(base, ours, theirs, prefer='ours')
        blocks, _ = MERGE.split_tops(merged)
        self.assertIn('"ours"', blocks['A'])
        self.assertIn('"new"', blocks['B'])

    def test_rejects_malformed_and_duplicate_libraries(self):
        bad = (
            '(kicad_symbol_lib (symbol "A")',
            '(kicad_symbol_lib (symbol "A))',
            library(symbol('A', 'one'), symbol('A', 'two')),
            library(symbol(r'A\"B', 'one'), symbol('A"B', 'two')),
        )
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(MERGE.MergeError):
                    MERGE.split_tops(text)

    def test_accepts_no_final_newline(self):
        text = library(symbol('A', 'one'), final_newline=False)
        self.assertEqual(MERGE.top_names(text), ['A'])
        merged = MERGE.merge_texts(text, text, text)
        self.assertTrue(merged.endswith('\n'))

    def test_diff_detects_full_block_change(self):
        base = library(symbol('A', 'old'))
        ours = library(symbol('A', 'new'))
        args = types.SimpleNamespace(base=None, ours=None, theirs=None,
                                     verbose=True)
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, text in (("base", base), ("ours", ours),
                               ("theirs", base)):
                path = Path(tmp, name)
                path.write_text(text)
                paths.append(str(path))
            args.base, args.ours, args.theirs = paths
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                MERGE.cmd_diff(args)
        self.assertIn('A: ours change', output.getvalue())
        self.assertIn('-\t\t(property "Value" "old")', output.getvalue())
        self.assertIn('+\t\t(property "Value" "new")', output.getvalue())

    def test_rejects_symbol_output_overlapping_input(self):
        with self.assertRaisesRegex(MERGE.MergeError, 'overlaps'):
            MERGE._require_distinct_output('/tmp/ours',
                                           ('/tmp/base', '/tmp/ours',
                                            '/tmp/theirs'))


class FootprintMergeTests(unittest.TestCase):
    def _write(self, directory, name, data):
        Path(directory, name).write_bytes(data)

    def _args(self, base, ours, theirs, output, prefer=None, dry_run=False):
        return types.SimpleNamespace(base=base, ours=ours, theirs=theirs,
                                     out=output, prefer=prefer,
                                     dry_run=dry_run)

    def test_unilateral_change_and_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ours, theirs, output = [Path(tmp, name) for name in
                                          ('base', 'ours', 'theirs', 'out')]
            for directory in (base, ours, theirs):
                directory.mkdir()
            self._write(base, 'changed.kicad_mod', b'old')
            self._write(ours, 'changed.kicad_mod', b'old')
            self._write(theirs, 'changed.kicad_mod', b'new')
            self._write(base, 'deleted.kicad_mod', b'delete me')
            self._write(ours, 'deleted.kicad_mod', b'delete me')
            MERGE.cmd_fp_merge(self._args(str(base), str(ours), str(theirs),
                                           str(output)))
            self.assertEqual(Path(output, 'changed.kicad_mod').read_bytes(),
                             b'new')
            self.assertFalse(Path(output, 'deleted.kicad_mod').exists())

    def test_conflict_aborts_and_prefer_does_not_override_clean_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ours, theirs, output = [Path(tmp, name) for name in
                                          ('base', 'ours', 'theirs', 'out')]
            for directory in (base, ours, theirs):
                directory.mkdir()
            for directory, data in ((base, b'old'), (ours, b'ours'),
                                    (theirs, b'theirs')):
                self._write(directory, 'conflict.kicad_mod', data)
            self._write(base, 'clean.kicad_mod', b'old')
            self._write(ours, 'clean.kicad_mod', b'old')
            self._write(theirs, 'clean.kicad_mod', b'new')
            with self.assertRaisesRegex(MERGE.MergeError, 'conflicts'):
                MERGE.cmd_fp_merge(self._args(
                    str(base), str(ours), str(theirs), str(output)))
            self.assertFalse(output.exists())
            MERGE.cmd_fp_merge(self._args(
                str(base), str(ours), str(theirs), str(output), 'ours'))
            self.assertEqual(Path(output, 'conflict.kicad_mod').read_bytes(),
                             b'ours')
            self.assertEqual(Path(output, 'clean.kicad_mod').read_bytes(),
                             b'new')

    def test_existing_output_is_replaced_without_stale_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ours, theirs, output = [Path(tmp, name) for name in
                                          ('base', 'ours', 'theirs', 'out')]
            for directory in (base, ours, theirs, output):
                directory.mkdir()
            self._write(ours, 'keep.kicad_mod', b'content')
            self._write(output, 'stale.kicad_mod', b'stale')
            MERGE.cmd_fp_merge(self._args(
                str(base), str(ours), str(theirs), str(output)))
            self.assertEqual(sorted(os.listdir(output)), ['keep.kicad_mod'])

    def test_rejects_output_overlapping_an_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, ours, theirs = [Path(tmp, name) for name in
                                  ('base', 'ours', 'theirs')]
            for directory in (base, ours, theirs):
                directory.mkdir()
            with self.assertRaisesRegex(MERGE.MergeError, 'overlaps'):
                MERGE.cmd_fp_merge(self._args(
                    str(base), str(ours), str(theirs), str(ours)))


if __name__ == '__main__':
    unittest.main()
