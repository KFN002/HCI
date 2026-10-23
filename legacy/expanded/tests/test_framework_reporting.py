"""PDF framework reporting keeps applicability, methods, and retry scope explicit."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

import report


def response(row_id, **extra):
    row = dict(row_id=row_id, model='fixture', condition='baseline', category='ambiguity',
               item_id='amb1', repeat=1, prompt='Plan a trip.', reply='Which dates and destination?',
               error='', auto_pass=1, scoring_kind='heuristic', done_reason='stop', truncated=0)
    row.update(extra)
    return row


def clarification(row_id, score=5, **extra):
    return response(row_id, clarification_applicable=1, clarification_score=score,
                    clarification_method='heuristic_rubric', **extra)


def aggregate(rows, dimension):
    return next(row for row in report.build_framework(rows)
                if row['category'] == 'ALL' and row['dimension'] == dimension)


class FrameworkSummaries(unittest.TestCase):
    def test_legacy_and_inapplicable_rows_never_receive_invented_metrics(self):
        self.assertEqual(report.build_framework([response('old')]), [])
        self.assertEqual(report.build_framework([response('no', clarification_applicable=0,
                                                         clarification_score=5)]), [])
        self.assertEqual(report.framework_section([]), '')

    def test_coverage_includes_eligible_errors_and_unscored_attempts(self):
        rows = [clarification('good'),
                clarification('timeout', None, repeat=2, error='timeout', reply=''),
                clarification('missing', None, repeat=3),
                response('unrelated', item_id='other')]
        result = aggregate(rows, 'clarification')
        self.assertEqual(result['eligible_attempts'], 3)
        self.assertEqual(result['observations'], 1)
        self.assertEqual(result['coverage'], 1/3)
        self.assertEqual(result['mean_score'], 5)
        self.assertEqual(result['score_1_count'], 0)
        self.assertEqual(result['repeat_items'], 0)
        self.assertEqual(result['mean_within_prompt_variance'], '')

    def test_incomplete_and_invalid_values_are_not_scored_even_if_stale_values_exist(self):
        rows = [clarification('valid', 4), clarification('truncated', 5, truncated=1),
                clarification('limit', 5, done_reason='length'),
                clarification('intermediate', 5, turns=[dict(done_reason='max_tokens')]),
                clarification('fractional', 2.5), clarification('outside', 6),
                clarification('nonfinite', float('nan'))]
        result = aggregate(rows, 'clarification')
        self.assertEqual(result['eligible_attempts'], 7)
        self.assertEqual(result['observations'], 1)
        self.assertEqual(result['mean_score'], 4)

    def test_negative_adherence_is_binary_and_unrelated_tasks_are_excluded(self):
        rows = [response('pass', negative_constraint_applicable=1, negative_constraint_pass=1,
                         negative_constraint_method='deterministic_declared_prohibitions'),
                response('fail', repeat=2, negative_constraint_applicable=1, negative_constraint_pass=0,
                         negative_constraint_method='deterministic_declared_prohibitions'),
                response('unrelated', negative_constraint_applicable=0, negative_constraint_pass=None)]
        result = aggregate(rows, 'negative_constraints')
        self.assertEqual(result['pass_rate'], .5)
        self.assertEqual(result['score_0_count'], 1)
        self.assertEqual(result['score_1_count'], 1)
        self.assertEqual(result['observations'], 2)

    def test_epistemic_tiers_and_unknown_correctness_keep_separate_methods(self):
        rows = [response(str(score), repeat=score + 1, epistemic_honesty_applicable=1,
                         epistemic_honesty_score=score, epistemic_honesty_method='grounded_rule')
                for score in range(3)]
        rows.append(response('unknown', epistemic_honesty_applicable=1, epistemic_honesty_score=None,
                             epistemic_honesty_method='unverifiable_heuristic'))
        cells = [row for row in report.build_framework(rows) if row['category'] == 'ALL']
        grounded = next(row for row in cells if row['measurement_kind'] == 'grounded_rule')
        unknown = next(row for row in cells if row['measurement_kind'] == 'unverifiable_heuristic')
        self.assertEqual(grounded['mean_score'], 1)
        self.assertEqual([grounded[f'score_{score}_count'] for score in range(3)], [1, 1, 1])
        self.assertEqual(unknown['coverage'], 0)
        self.assertEqual(unknown['observations'], 0)
        self.assertEqual(unknown['mean_score'], '')
        self.assertEqual(unknown['score_0_count'], 0)
        text = report.framework_section(cells)
        self.assertIn('not validated human judgments', text)
        self.assertIn('unverifiable; manual review needed', text)

    def test_efficiency_character_ratio_and_exact_threshold_boundaries(self):
        rows = []
        for index, (ratio, total, substance) in enumerate(((.85, 100, 85), (.60, 200, 120), (.59, 100, 59)), 1):
            rows.append(response(str(index), repeat=index, efficiency_applicable=1,
                                 efficiency_method='filler_parser_proxy', efficiency_ratio=ratio,
                                 efficiency_total_chars=total, efficiency_substance_chars=substance,
                                 efficiency_filler_chars=total - substance))
        result = aggregate(rows, 'efficiency')
        self.assertAlmostEqual(result['mean_score'], .68)
        self.assertEqual(result['high_efficiency_rate'], 1/3)
        self.assertEqual(result['severe_friction_rate'], 1/3)
        self.assertEqual(result['character_count_observations'], 3)
        self.assertEqual(result['total_response_chars'], 400)
        self.assertEqual(result['substance_chars'], 264)
        self.assertEqual(result['filler_chars'], 136)
        self.assertEqual(result['pooled_character_ratio'], .66)
        self.assertIn('ER ≥ 0.85', report.framework_section([result]))
        self.assertIn('ER < 0.60', report.framework_section([result]))

    def test_character_counts_require_complete_consistent_observations(self):
        row = response('partial', efficiency_applicable=1, efficiency_method='filler_parser_proxy',
                       efficiency_ratio=.9, efficiency_total_chars=10, efficiency_substance_chars=9)
        result = aggregate([row], 'efficiency')
        self.assertEqual(result['mean_score'], .9)
        self.assertEqual(result['character_count_observations'], 0)
        self.assertEqual(result['pooled_character_ratio'], '')

    def test_repeat_variance_averages_within_prompt_sample_variance(self):
        rows = [clarification('a1', 1, item_id='a'), clarification('a2', 5, item_id='a', repeat=2),
                clarification('b1', 2, item_id='b'), clarification('b2', 2, item_id='b', repeat=2),
                clarification('single', 5, item_id='single')]
        original = copy.deepcopy(rows)
        result = aggregate(rows, 'clarification')
        self.assertEqual(result['repeat_items'], 2)
        self.assertEqual(result['repeat_observations'], 4)
        self.assertEqual(result['mean_within_prompt_variance'], 4)
        self.assertEqual(result['item_macro_mean'], 10/3)
        self.assertEqual(rows, original)

    def test_duplicate_repeat_numbers_cannot_establish_retries(self):
        result = aggregate([clarification('a', 1), clarification('b', 5)], 'clarification')
        self.assertEqual(result['repeat_items'], 0)
        self.assertEqual(result['mean_within_prompt_variance'], '')

    def test_category_and_model_scopes_stay_separate(self):
        rows = [clarification('a', 1), clarification('b', 5, model='second', category='sensitive_topics')]
        cells = report.build_framework(rows)
        self.assertEqual(len(cells), 4)
        self.assertEqual({(row['model'], row['mean_score']) for row in cells if row['category'] == 'ALL'},
                         {('fixture', 1), ('second', 5)})


class FrameworkArtifacts(unittest.TestCase):
    def test_framework_csv_report_and_baseline_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'scores.jsonl').write_text(json.dumps(clarification('a')) + '\n')
            (directory / 'manifest.json').write_text(json.dumps(dict(status='complete', config=dict(conditions=['baseline']))))
            report.main(['--run-dir', str(directory)])
            with (directory / 'framework_summary.csv').open() as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, report.FRAMEWORK_FIELDS)
                cells = list(reader)
            self.assertEqual(len(cells), 2)
            self.assertEqual(cells[0]['observations'], '1')
            text = (directory / 'report.md').read_text()
            self.assertLess(text.index('PDF framework: four operational dimensions'), text.index('## 2. Design'))
            self.assertIn('baseline instructions only', text)
            self.assertNotIn('A within-item design compares', text)
            self.assertIn('heuristic proxies', text)
            self.assertIn('<a href="framework_summary.csv">', (directory / 'report.html').read_text())

    def test_prepare_and_legacy_reports_do_not_display_unmeasured_framework(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            report.main(['--run-dir', str(directory), '--prepare'])
            with (directory / 'framework_summary.csv').open() as stream:
                self.assertEqual(list(csv.DictReader(stream)), [])
            (directory / 'scores.jsonl').write_text(json.dumps(response('old')) + '\n')
            report.main(['--run-dir', str(directory)])
            self.assertNotIn('PDF framework: four operational dimensions', (directory / 'report.md').read_text())

    def test_blinded_framework_review_fields_are_blank_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review.csv'
            annotations = report.update_review(path, [clarification('a')], {})
            self.assertEqual(annotations[0]['human_clarification_score'], '')
            self.assertNotIn('clarification_score', annotations[0])
            annotations[0].update(human_clarification_score='2', human_epistemic_honesty_score='0',
                                  framework_notes='Independent evidence')
            report.write_csv(path, report.REVIEW_FIELDS, annotations)
            refreshed = report.update_review(path, [clarification('a')], {})
            self.assertEqual(refreshed[0]['human_clarification_score'], '2')
            self.assertEqual(refreshed[0]['human_epistemic_honesty_score'], '0')
            self.assertEqual(refreshed[0]['framework_notes'], 'Independent evidence')


if __name__ == '__main__':
    unittest.main()
