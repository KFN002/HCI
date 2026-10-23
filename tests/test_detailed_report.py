"""Detailed evidence reports and derived selected cohorts preserve source runs."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import detailed_report


def item(identity):
    return dict(id=identity, category='category-' + identity, prompt='Prompt ' + identity,
                rubric='Rubric ' + identity, framework={})


def response(model, identity, repeat):
    return dict(row_id=model + '-' + identity + str(repeat), model=model, item_id=identity, category='category-' + identity,
                repeat=repeat, prompt='Prompt ' + identity, reply='Which dates? Which destination?',
                error='', truncated=False, clarification_applicable=1, clarification_score=5,
                clarification_method='heuristic_rubric', clarification_reason='Two targeted questions.')


def source(directory, models, identities, *, status='partial', missing=False):
    directory.mkdir()
    suite = dict(items=[item(identity) for identity in identities], design=dict(item_count=len(identities)))
    config = dict(repeats=3, temperature=.5, num_predict=512, num_ctx=4096, seed=42,
                  condition='baseline', scorer_version='3.0-simple', suite_sha256=str(len(identities)),
                  models=[dict(name=model, digest='digest-' + model) for model in models])
    manifest = dict(status=status, planned_jobs=len(models) * len(identities) * 3, config=config)
    rows = [response(model, identity, repeat) for model in models for identity in identities for repeat in range(1, 4)]
    if missing:
        rows.pop()
    manifest['completed_jobs'] = len(rows)
    for filename, value in [('manifest.json', manifest), ('prompts.snapshot.json', suite)]:
        (directory / filename).write_text(json.dumps(value))
    (directory / 'scores.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return rows, manifest, suite


class DetailedReports(unittest.TestCase):
    def test_inline_figure_ids_and_references_are_namespaced_without_changing_exports(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg"><g id="figure_1"><clipPath id="clip_1"/>'
               '<path id="marker_1"/><use href="#marker_1"/><use xlink:href="#marker_1"/>'
               '<g clip-path="url(#clip_1)"/></g></svg>')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows, manifest, suite = source(root / 'source', ['one'], ['a'], status='complete')

            def fake_charts(output, *_):
                directory = Path(output) / 'charts'
                directory.mkdir()
                for name in ('first.svg', 'second.svg'):
                    (directory / name).write_text(svg)
                return [dict(file='charts/' + name, png='charts/unused.png', title=name)
                        for name in ('first.svg', 'second.svg')]

            with patch('detailed_report.plot_report.build', side_effect=fake_charts):
                detailed_report.generate(root / 'report', rows, manifest, suite)
            page = (root / 'report/report.html').read_text()
            identities = re.findall(r'\bid="([^"]+)"', page)
            self.assertEqual(len(identities), len(set(identities)))
            for index in range(2):
                self.assertIn(f'id="chart{index}-figure_1"', page)
                self.assertIn(f'href="#chart{index}-marker_1"', page)
                self.assertIn(f'xlink:href="#chart{index}-marker_1"', page)
                self.assertIn(f'url(#chart{index}-clip_1)', page)
            self.assertEqual((root / 'report/charts/first.svg').read_text(), svg)
            self.assertEqual((root / 'report/charts/second.svg').read_text(), svg)

    def test_full_report_contains_expandable_evidence_and_escapes_source_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows, manifest, suite = source(root / 'source', ['one'], ['a'], status='complete')
            rows[0].update(reply='<script>quoted evidence</script>', thinking='Unscored provider field')
            detailed_report.generate(root / 'report', rows, manifest, suite)
            page = (root / 'report/report.html').read_text()
            for phrase in ('COMPLETE — selected cohort', 'Protocol and measurement definitions', 'Category results',
                           'Repeated-prompt score variance', 'Highest and lowest automatic scores',
                           'Full prompts, rubrics, responses', 'Two targeted questions.', 'Rubric a',
                           'think=None', 'Reduced prompt coverage', 'Source scope'):
                self.assertIn(phrase, page)
            self.assertIn('&lt;script&gt;', page)
            self.assertNotIn('<script>', page)
            self.assertIn('<details', page)
            self.assertIn('Provider-returned thinking field — unscored', page)
            self.assertIn('Generation token cap', page)

    def test_selected_shared_subset_can_be_complete_when_original_source_is_partial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source(root / 'broader', ['one', 'excluded'], ['a', 'b'])
            source(root / 'quick', ['two'], ['a'], status='complete')
            originals = {str(path): path.read_bytes() for directory in (root / 'broader', root / 'quick')
                         for path in directory.iterdir()}
            summary = detailed_report.generate_comparison(root / 'report', [root / 'broader', root / 'quick'], ['one', 'two'])
            manifest = json.loads((root / 'report/manifest.json').read_text())
            self.assertEqual(manifest['status'], 'complete')
            self.assertEqual(manifest['planned_jobs'], 6)
            self.assertEqual(manifest['completed_jobs'], 6)
            self.assertEqual(manifest['excluded_source_observations'], 9)
            self.assertEqual(len(summary), 2)
            self.assertEqual(manifest['source_runs'][0]['manifest']['status'], 'partial')
            rows = [json.loads(line) for line in (root / 'report/scores.jsonl').read_text().splitlines()]
            self.assertEqual(len({row['row_id'] for row in rows}), 6)
            self.assertEqual({row['item_id'] for row in rows}, {'a'})
            self.assertTrue(all(row['source_row_id'].endswith('a' + str(row['repeat'])) for row in rows))
            for path, original in originals.items():
                self.assertEqual(Path(path).read_bytes(), original)

    def test_partial_selected_cohort_does_not_invent_missing_replies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source(root / 'one', ['one'], ['a'])
            source(root / 'two', ['two'], ['a'], missing=True)
            result = detailed_report.generate_comparison(root / 'report', [root / 'one', root / 'two'])
            manifest = json.loads((root / 'report/manifest.json').read_text())
            self.assertEqual(manifest['status'], 'partial')
            self.assertEqual(manifest['completed_jobs'], 5)
            self.assertEqual(next(row for row in result if row['model'] == 'two')['clarification_mean'], 5)
            self.assertIn('PARTIAL', (root / 'report/report.html').read_text())

    def test_different_shared_content_is_rejected_before_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source(root / 'one', ['one'], ['a'])
            source(root / 'two', ['two'], ['a'])
            path = root / 'two/prompts.snapshot.json'
            changed = json.loads(path.read_text())
            changed['items'][0]['rubric'] = 'Different evaluation rubric'
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'content or rubric differs'):
                detailed_report.generate_comparison(root / 'report', [root / 'one', root / 'two'])
            self.assertFalse((root / 'report').exists())

    def test_generation_settings_must_match_even_with_different_runner_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source(root / 'one', ['one'], ['a'])
            source(root / 'two', ['two'], ['a'])
            path = root / 'two/manifest.json'
            changed = json.loads(path.read_text())
            changed['config']['temperature'] = .9
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'measurement settings differ'):
                detailed_report.generate_comparison(root / 'report', [root / 'one', root / 'two'])

    def test_duplicate_model_item_repeat_cells_cannot_be_pooled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source(root / 'one', ['same'], ['a'])
            source(root / 'two', ['same'], ['a'])
            with self.assertRaisesRegex(ValueError, 'Duplicate or out-of-plan'):
                detailed_report.generate_comparison(root / 'report', [root / 'one', root / 'two'])

    def test_explicit_selected_items_must_be_shared_and_output_must_be_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source(root / 'one', ['one'], ['a', 'b'])
            source(root / 'two', ['two'], ['a'])
            with self.assertRaisesRegex(ValueError, 'every source snapshot'):
                detailed_report.generate_comparison(root / 'report', [root / 'one', root / 'two'], selected_items=['b'])
            with self.assertRaisesRegex(ValueError, 'separate output'):
                detailed_report.generate_comparison(root / 'one', [root / 'one'])


if __name__ == '__main__':
    unittest.main()
