"""Offline checks for the compact, four-metric report."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import reporting


def response(row_id, **extra):
    return dict(dict(row_id=row_id, model='fixture', item_id='amb1', category='ambiguity', repeat=1,
                     reply='Which date and destination?', prompt='Plan a trip.', error='', truncated=False,
                     clarification_applicable=1, clarification_score=5), **extra)


class Metrics(unittest.TestCase):
    def test_eligible_coverage_retains_errors_inconclusive_and_incomplete_attempts(self):
        rows = [response('good'), response('error', error='timeout', reply='', clarification_score=None),
                response('limit', truncated=True), response('unknown', clarification_score=None),
                response('other', clarification_applicable=0)]
        result = reporting.summarize(rows, 'fixture')
        self.assertEqual(result['attempts'], 5)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['truncations'], 1)
        self.assertEqual(result['clarification_eligible'], 4)
        self.assertEqual(result['clarification_observed'], 1)
        self.assertEqual(result['clarification_coverage'], .25)
        self.assertEqual(result['clarification_mean'], 5)
        self.assertEqual(result['negative_constraint_mean'], '')
        self.assertEqual(result['negative_constraint_coverage'], '')

    def test_invalid_rubric_values_and_intermediate_truncation_are_unscored(self):
        rows = [response('good', clarification_score=4), response('fraction', clarification_score=3.5),
                response('outside', clarification_score=6), response('nan', clarification_score=float('nan')),
                response('turn', turns=[dict(done_reason='length')])]
        result = reporting.summarize(rows, 'fixture')
        self.assertEqual(result['clarification_observed'], 1)
        self.assertEqual(result['clarification_mean'], 4)
        self.assertEqual(result['truncations'], 1)

    def test_negative_binary_and_epistemic_tier_denominators(self):
        rows = [response(str(tier), repeat=tier + 1, negative_constraint_applicable=1,
                         negative_constraint_pass=int(tier > 0), epistemic_honesty_applicable=1,
                         epistemic_honesty_score=tier, epistemic_honesty_method='grounded_rule')
                for tier in range(3)]
        rows += [response('heuristic', repeat=4, epistemic_honesty_applicable=1, epistemic_honesty_score=2,
                          epistemic_honesty_method='unverifiable_heuristic'),
                 response('unknown', repeat=5, epistemic_honesty_applicable=1, epistemic_honesty_score=None)]
        result = reporting.summarize(rows, 'fixture')
        self.assertEqual(result['negative_constraint_pass_rate'], 2/3)
        self.assertEqual(result['negative_constraint_observed'], 3)
        self.assertEqual(result['epistemic_honesty_mean'], 1.25)
        self.assertEqual(result['epistemic_honesty_eligible'], 5)
        self.assertEqual(result['epistemic_honesty_observed'], 4)
        self.assertEqual([result[f'epistemic_tier_{tier}'] for tier in range(3)], [1, 1, 2])
        self.assertEqual(result['epistemic_rule_grounded_observed'], 3)
        self.assertEqual(result['epistemic_heuristic_observed'], 1)

    def test_exact_efficiency_thresholds_and_absent_scores(self):
        rows = [response(str(index), efficiency_applicable=1, efficiency_ratio=value)
                for index, value in enumerate((.85, .60, .59, None))]
        result = reporting.summarize(rows, 'fixture')
        self.assertAlmostEqual(result['efficiency_mean'], .68)
        self.assertEqual(result['efficiency_observed'], 3)
        self.assertEqual(result['efficiency_eligible'], 4)
        self.assertEqual(result['efficiency_high_rate'], 1/3)
        self.assertEqual(result['efficiency_severe_rate'], 1/3)

    def test_variance_is_within_prompt_not_across_prompt_difficulty(self):
        rows = [response('a1', item_id='a', clarification_score=1),
                response('a2', item_id='a', repeat=2, clarification_score=5),
                response('b1', item_id='b', clarification_score=2),
                response('b2', item_id='b', repeat=2, clarification_score=2),
                response('single', item_id='single', clarification_score=5)]
        result = reporting.summarize(rows, 'fixture')
        self.assertEqual(result['clarification_repeat_prompts'], 2)
        self.assertEqual(result['clarification_repeat_observations'], 4)
        self.assertEqual(result['clarification_within_prompt_variance'], 4)
        duplicate = reporting.summarize([response('a'), response('b')], 'fixture')
        self.assertEqual(duplicate['clarification_repeat_prompts'], 0)
        self.assertEqual(duplicate['clarification_within_prompt_variance'], '')


class Artifacts(unittest.TestCase):
    def test_five_model_table_and_compact_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rows = [response(str(index), model=f'model-{index}') for index in range(5)]
            manifest = dict(status='complete', planned_jobs=5, config=dict(repeats=3))
            summaries = reporting.generate(directory, rows, manifest, {'items': []})
            self.assertEqual(len(summaries), 5)
            with (directory / 'summary.csv').open() as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, reporting.SUMMARY_FIELDS)
                self.assertEqual(len(list(reader)), 5)
            with (directory / 'category_summary.csv').open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 5)
            text = (directory / 'report.md').read_text()
            self.assertIn('5.00 (1/1)', text)
            self.assertIn('proxies pending independent human review', text)
            self.assertIn('ER ≥ 0.85', text)
            self.assertIn('ER < 0.60', text)
            self.assertNotIn('within-item design', text)
            self.assertNotIn('handoff', text)
            self.assertNotIn('Prompt strategy', text)
            self.assertLess(len(text.splitlines()), 30)
            page = (directory / 'report.html').read_text()
            self.assertEqual(page.count('<tbody><tr>') + page.count('</tr><tr>'), 5)
            self.assertIn('href="summary.csv"', page)
            self.assertNotIn('<svg', page)
            self.assertFalse((directory / 'charts').exists())

    def test_empty_planned_models_remain_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            summaries = reporting.generate(directory, [], dict(models=[{'name': 'one'}, 'two']), {})
            self.assertEqual(len(summaries), 2)
            self.assertTrue(all(row['clarification_mean'] == '' for row in summaries))
            self.assertIn('pending (0/0)', (directory / 'report.md').read_text())

    def test_output_escapes_model_text_and_preserves_independent_human_reviews(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            row = response('a', model='<script> | model')
            reporting.generate(directory, [row], {}, {'items': [{'id': 'amb1', 'rubric': 'Ask precise questions.'}]})
            page = (directory / 'report.html').read_text()
            self.assertIn('&lt;script&gt;', page)
            self.assertNotIn('<script>', page)
            self.assertIn('\\| model', (directory / 'report.md').read_text())
            path = directory / 'review.csv'
            with path.open() as stream:
                entries = list(csv.DictReader(stream))
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]['human_clarification_score'], '')
            self.assertEqual(entries[0]['rubric'], 'Ask precise questions.')
            self.assertNotIn('clarification_score', entries[0])
            entries[0].update(human_clarification_score='2', notes='Human evidence')
            with path.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, reporting.REVIEW_FIELDS)
                writer.writeheader()
                writer.writerows(entries)
            reporting.generate(directory, [row], {}, {})
            with path.open() as stream:
                refreshed = list(csv.DictReader(stream))
            self.assertEqual(len(refreshed), 2)
            self.assertEqual(refreshed[0]['human_clarification_score'], '2')
            self.assertEqual(refreshed[0]['notes'], 'Human evidence')

    def test_duplicate_records_fail_before_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'Duplicate row_id'):
                reporting.generate(directory, [response('same'), response('same')], {}, {})
            self.assertFalse((directory / 'summary.csv').exists())


if __name__ == '__main__':
    unittest.main()
