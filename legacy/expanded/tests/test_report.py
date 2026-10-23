"""Offline analysis tests; no provider is contacted and no model is loaded."""

import csv
import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import report
from score import score_response


def response(row_id, *, item_id='ctl1', condition='baseline', repeat=1,
             auto_pass=1, error='', reply='42', **extra):
    row = dict(row_id=row_id, model='ollama:fixture', condition=condition,
               category='control', item_id=item_id, repeat=repeat,
               prompt='Return only the answer.', reply=reply, error=error,
               auto_pass=auto_pass, scoring_kind='objective', seconds=2.0,
               words=1, ttft_seconds=.5, tokens_per_second=10.0,
               done_reason='stop', truncated=0)
    return {**row, **extra}


class Summaries(unittest.TestCase):
    def test_errors_and_unscored_have_explicit_denominators(self):
        rows = [response('a'), response('b', auto_pass=0),
                response('c', auto_pass='', error='timeout', reply=''),
                response('d', auto_pass='')]
        summary = report.summarise(rows, 'ollama:fixture', 'baseline', 'control')
        self.assertEqual(summary['attempts'], 4)
        self.assertEqual(summary['valid_responses'], 3)
        self.assertEqual(summary['scored'], 2)
        self.assertEqual(summary['errors'], 1)
        self.assertEqual(summary['conservative_pass_rate'], .25)
        self.assertEqual(summary['pass_rate_valid'], .5)

    def test_absence_is_not_zero(self):
        summary = report.summarise([], 'ollama:fixture', 'baseline', 'control')
        self.assertEqual(summary['median_seconds'], '')
        self.assertEqual(summary['pass_rate_valid'], '')
        self.assertEqual(summary['mean_brier_score'], '')
        self.assertEqual(summary['calibration_format_rate'], '')

    def test_wilson_boundaries(self):
        self.assertEqual(report.wilson(0, 0), ('', ''))
        low, high = report.wilson(0, 5)
        self.assertAlmostEqual(low, 0)
        self.assertGreater(high, 0)
        low, high = report.wilson(5, 5)
        self.assertLess(low, 1)
        self.assertAlmostEqual(high, 1)
        self.assertEqual(report.percentile([1, 2, 3], .95), 2.9)

    def test_intermediate_truncation_counts_even_final_turn_stops(self):
        summary = report.summarise([response('a', truncated=1, done_reason='stop')], 'm', 'c', 'control')
        self.assertEqual(summary['truncated_count'], 1)
        self.assertEqual(summary['truncation_rate'], 1)

    def test_consistency_requires_repeats_and_all_fail_is_stable(self):
        self.assertEqual(report.consistency([response('a', auto_pass=0)]), '')
        self.assertEqual(report.consistency([response('a', auto_pass=0), response('b', auto_pass=0, repeat=2)]), 1)

    def test_calibration_coverage_exposes_selective_missing_probabilities(self):
        rows = [response('a', category='confidence_calibration', brier_score=.04,
                         confidence=.8, high_confidence_error=0),
                response('b', category='confidence_calibration', auto_pass=0),
                response('c', category='confidence_calibration', error='timeout', auto_pass='', reply='')]
        summary = report.summarise(rows, 'm', 'baseline', 'confidence_calibration')
        self.assertEqual(summary['calibration_attempts'], 3)
        self.assertEqual(summary['calibration_valid_outputs'], 1)
        self.assertEqual(summary['calibration_format_rate'], 1/3)
        self.assertEqual(summary['mean_brier_score'], .04)
        self.assertEqual(summary['high_confidence_error_rate'], 0)

    def test_strategy_comparison_pairs_only_matching_items_and_repeats(self):
        rows = [response('a'), response('b', condition='structured', auto_pass=0),
                response('c', condition='structured', item_id='not_matched'),
                response('d', item_id='second', error='timeout', reply='', auto_pass=''),
                response('e', item_id='second', condition='structured')]
        aggregate = [row for row in report.compare_strategies(rows) if row['category'] == 'ALL'][0]
        self.assertEqual(aggregate['matched_pairs'], 2)
        self.assertEqual(aggregate['matched_items'], 2)
        self.assertEqual(aggregate['wins'], 1)
        self.assertEqual(aggregate['losses'], 1)
        self.assertEqual(aggregate['paired_delta'], 0)


class Annotation(unittest.TestCase):
    def test_regeneration_preserves_labels_and_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review.csv'
            rows = [response('a')]
            annotations = report.update_review(path, rows, {})
            annotations[0].update(human_pass='0', notes='Independent note')
            report.write_csv(path, report.REVIEW_FIELDS, annotations)
            refreshed = report.update_review(path, rows, {})
            self.assertEqual(len(refreshed), 2)
            self.assertEqual(refreshed[0]['human_pass'], '0')
            self.assertEqual(refreshed[0]['notes'], 'Independent note')
            self.assertNotIn('auto_pass', refreshed[0])

    def test_duplicate_rated_annotation_is_rejected(self):
        annotation = dict(row_id='a', rater='rater_1', human_pass='1')
        with self.assertRaisesRegex(ValueError, 'Duplicate human annotation'):
            report.agreement([annotation, annotation], [response('a')])

    def test_rater_agreement_is_separate_from_auto_agreement(self):
        rows = [response('a'), response('b', auto_pass=0)]
        labels = [dict(row_id='a', rater='rater_1', human_pass='1'),
                  dict(row_id='a', rater='rater_2', human_pass='0'),
                  dict(row_id='b', rater='rater_1', human_pass='0'),
                  dict(row_id='b', rater='rater_2', human_pass='0')]
        result = report.agreement(labels, rows)
        self.assertEqual(result['auto_human_agreement'], .75)
        self.assertEqual(result['inter_rater_agreement'], .5)
        self.assertEqual(result['double_rated'], 2)


class Artifacts(unittest.TestCase):
    def test_prepare_then_partial_report_and_duplicate_data_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            report.main(['--run-dir', str(run_dir), '--prepare'])
            self.assertIn('NOT RUN', (run_dir / 'report.md').read_text())
            with (run_dir / 'summary.csv').open() as stream:
                self.assertEqual(list(csv.DictReader(stream)), [])
            (run_dir / 'scores.jsonl').write_text(json.dumps(response('a')) + '\n')
            (run_dir / 'manifest.json').write_text(json.dumps(dict(status='interrupted', planned_jobs=2, planned_requests=3)))
            report.main(['--run-dir', str(run_dir)])
            self.assertIn('PARTIAL', (run_dir / 'report.md').read_text())
            self.assertIn('Planned inference requests', (run_dir / 'report.md').read_text())
            page = (run_dir / 'report.html').read_text()
            self.assertIn('<table>', page)
            self.assertIn('<h2>', page)
            self.assertNotIn('<pre>', page)
            (run_dir / 'scores.jsonl').write_text((json.dumps(response('a')) + '\n') * 2)
            with self.assertRaisesRegex(ValueError, 'Duplicate row_id'):
                report.main(['--run-dir', str(run_dir)])

    def test_renderer_escapes_content_and_formats_table(self):
        rendered = report.markdown_html('# Title\n\n<script>bad()</script>\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n')
        self.assertIn('<h1>Title</h1>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertIn('<th>A</th>', rendered)
        self.assertIn('<td>2</td>', rendered)

    def test_long_model_labels_are_clipped_but_full_name_retained(self):
        name = 'ollama:' + 'longmodel' * 20
        svg = report.chart_svg([dict(model=name, condition='baseline', category='ALL', conservative_pass_rate=.5)])
        self.assertIn('<title>' + name + ' / baseline</title>', svg)
        self.assertIn('…', svg)

    def test_real_suite_uppercase_contract_rejects_lowercase(self):
        suite = json.loads((Path(__file__).resolve().parents[1] / 'prompts.json').read_text())
        item = next(item for item in suite['items'] if item['id'] == 'con3')
        self.assertEqual(score_response(item, 'RED')['auto_pass'], 1)
        self.assertEqual(score_response(item, 'red')['auto_pass'], 0)


class ExpandedResearchMetrics(unittest.TestCase):
    def test_handoff_errors_and_guessing_use_different_denominators(self):
        rows = [response('a', item_id='amb', research_role='needs_clarification', appropriate_handoff=1,
                         speculative_guess_detected=0, clarification_requested=1, clarification_specificity=.5),
                response('b', item_id='unk', research_role='unanswerable', appropriate_handoff=0,
                         speculative_guess_detected=1, uncertainty_acknowledged=1),
                response('c', item_id='amb', repeat=2, research_role='needs_clarification',
                         error='timeout', reply='', appropriate_handoff='', speculative_guess_detected='')]
        cell = report.build_handoff(rows)[0]
        self.assertEqual(cell['primary_attempts'], 3)
        self.assertEqual(cell['primary_items'], 2)
        self.assertEqual(cell['primary_metric_observations'], 2)
        self.assertEqual(cell['handoff_rate_all'], 1/3)
        self.assertEqual(cell['handoff_rate_valid'], .5)
        self.assertEqual(cell['guess_rate'], .5)
        self.assertEqual(cell['guess_observations'], 2)
        self.assertEqual(cell['handoff_item_macro'], .25)
        self.assertEqual(cell['mean_clarification_specificity'], .5)

    def test_controls_and_followup_are_not_in_primary_handoff_scope(self):
        rows = [response('a', research_role='answerable_control', unnecessary_clarification=1,
                         direct_answer_correct=0),
                response('b', item_id='resolved', research_role='resolved_followup', resolution_success=1)]
        cell = report.build_handoff(rows)[0]
        self.assertEqual(cell['primary_attempts'], 0)
        self.assertEqual(cell['handoff_rate_all'], '')
        self.assertEqual(cell['overclarification_rate'], 1)
        self.assertEqual(cell['direct_answer_rate'], 0)
        self.assertEqual(cell['resolution_rate'], 1)

    def test_old_data_never_receives_an_invented_zero_handoff_score(self):
        cell = report.build_handoff([response('a', research_role='needs_clarification')])[0]
        self.assertEqual(cell['primary_metric_observations'], 0)
        self.assertEqual(cell['handoff_rate_all'], '')
        self.assertEqual(cell['handoff_rate_valid'], '')
        self.assertEqual(cell['guess_rate'], '')

    def test_all_errors_still_show_conservative_handoff_failure(self):
        cell = report.build_handoff([response('a', research_role='unanswerable', reply='',
                                              error='timeout', appropriate_handoff='',
                                              speculative_guess_detected='')])[0]
        self.assertEqual(cell['handoff_rate_all'], 0)
        self.assertEqual(cell['handoff_rate_valid'], '')
        self.assertIn('Handoff / all', report.research_section([cell]))

    def test_prompt_cluster_bootstrap_uses_prompts_not_number_of_repeats(self):
        self.assertEqual(report.cluster_interval([1]), ('', ''))
        first = report.cluster_interval([0, 1])
        self.assertEqual(first, report.cluster_interval([0, 1]))
        self.assertEqual(first, (0, 1))
        # Degenerate equal prompt rates need no invented variability.
        self.assertEqual(report.cluster_interval([.5, .5]), (.5, .5))

    def test_repeat_text_diversity_and_adjacent_outcome_changes(self):
        rows = [response('a', reply='same', auto_pass=1),
                response('b', reply='same', auto_pass=0, repeat=2),
                response('c', reply='different', auto_pass=1, repeat=3)]
        result = report.repeat_diagnostics(rows)
        self.assertEqual(result['distinct_response_ratio'], 2/3)
        self.assertEqual(result['pass_flip_rate'], 1)
        self.assertEqual(result['pass_flip_transitions'], 2)
        self.assertEqual(report.repeat_diagnostics([response('one')])['pass_flip_rate'], '')

    def test_leakage_denominator_excludes_unrelated_controls(self):
        rows = [response('a', category='privacy', forbidden_leak=1)]
        rows += [response(str(i), forbidden_leak=0) for i in range(9)]
        summary = report.summarise(rows, 'm', 'baseline', 'ALL')
        self.assertEqual(summary['forbidden_leak_observations'], 1)
        self.assertEqual(summary['forbidden_leak_rate'], 1)
        self.assertEqual(report.summarise(rows[1:], 'm', 'baseline', 'control')['forbidden_leak_rate'], '')

    def test_missing_telemetry_is_not_a_measured_zero_in_tidy_export(self):
        rows = [response('a', load_seconds=1), response('b', load_seconds=None)]
        summary = report.summarise(rows, 'm', 'baseline', 'ALL')
        tidy = {row['metric']: row for row in report.metric_table([summary], [])}
        self.assertEqual(tidy['median_load_seconds']['value'], 1)
        self.assertEqual(tidy['median_load_seconds']['denominator'], 1)
        self.assertEqual(tidy['pass_rate_valid']['denominator'], 2)

    def test_matched_handoff_strategy_deltas_do_not_use_unmatched_prompts(self):
        rows = [response('a', appropriate_handoff=0),
                response('b', condition='structured', appropriate_handoff=1),
                response('c', item_id='extra_baseline', appropriate_handoff=1)]
        aggregate = next(row for row in report.compare_strategies(rows) if row['category'] == 'ALL')
        self.assertEqual(aggregate['handoff_matched_pairs'], 1)
        self.assertEqual(aggregate['handoff_item_macro_delta'], 1)

    def test_confidence_ece_has_coverage_and_no_missing_probabilities(self):
        rows = [response('a', confidence=.8, answer_correct=1),
                response('b', confidence=.6, answer_correct=0), response('c')]
        ece, gap = report.calibration_error(rows)
        self.assertAlmostEqual(ece, .4)
        self.assertAlmostEqual(gap, .2)
        self.assertEqual(report.calibration_error([response('empty')]), ('', ''))


class ExpandedCharts(unittest.TestCase):
    def test_no_data_means_no_graphs_and_header_only_new_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.assertEqual(report.build_charts(directory, [], [], [], []), [])
            report.main(['--run-dir', str(directory), '--prepare'])
            for filename in ('handoff_summary.csv', 'metric_summary.csv', 'review_candidates.csv'):
                with (directory / filename).open() as handle:
                    self.assertEqual(list(csv.DictReader(handle)), [])

    def test_graphs_export_valid_svg_and_embed_in_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = [response('a', research_role='needs_clarification', appropriate_handoff=1,
                             speculative_guess_detected=0, clarification_requested=1),
                    response('b', research_role='needs_clarification', appropriate_handoff=0,
                             speculative_guess_detected=1, repeat=2)]
            (directory / 'scores.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in data))
            report.main(['--run-dir', str(directory)])
            charts = json.loads((directory / 'charts/index.json').read_text())
            self.assertGreaterEqual(len(charts), 7)
            for chart in charts:
                root = ET.fromstring((directory / chart['file']).read_text())
                self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
                self.assertGreater(float(root.attrib['width']), 0)
                self.assertGreater(float(root.attrib['height']), 0)
            page = (directory / 'report.html').read_text()
            markdown = (directory / 'report.md').read_text()
            self.assertIn('<svg', page)
            self.assertIn('charts/handoff_guessing.svg', markdown)
            self.assertIn('Does the model hand control back or guess?', page)
            self.assertIn('Prompt-cluster', markdown)
            self.assertIn('height:auto', page)

    def test_heatmap_marks_missing_category_grey_and_escapes_names(self):
        first = report.summarise([response('a', category='control')], '<unsafe>', 'baseline', 'control')
        second = report.summarise([response('b', category='privacy')], 'other', 'baseline', 'privacy')
        svg = report.heatmap_svg([first, second])
        ET.fromstring(svg)
        self.assertIn('fill="#e2e8f0"', svg)
        self.assertIn('&lt;unsafe&gt;', svg)
        self.assertNotIn('<unsafe>', svg)

    def test_reliability_display_guard_does_not_fabricate_bins(self):
        self.assertIsNone(report.reliability_svg([response('a', confidence=.9, answer_correct=1)]))
        rows = [response(str(i), item_id=f'item{i % 3}', confidence=.9, answer_correct=1) for i in range(12)]
        ET.fromstring(report.reliability_svg(rows))

    def test_svg_bars_keep_missing_values_pending_and_long_names_available(self):
        name = '<model>' * 20
        svg = report.svg_bars('Metric', 'Missingness matters', [dict(model=name, condition='baseline', x='')], [('x', 'Observed', '#2563eb')])
        ET.fromstring(svg)
        self.assertIn('pending', svg)
        self.assertIn('&lt;model&gt;', svg)
        self.assertIn('…', svg)

    def test_chart_labels_show_observed_scope_for_unmatched_conditions(self):
        svg = report.svg_bars('Observed scope', 'Use paired comparisons',
            [dict(model='m', condition='baseline', distinct_items=72, attempts=216, x=.9),
             dict(model='m', condition='structured', distinct_items=18, attempts=54, x=.8)],
            [('x', 'Observed rate', '#2563eb')], percent_axis=True)
        self.assertIn('72 prompts / 216 attempts', svg)
        self.assertIn('18 prompts / 54 attempts', svg)

    def test_strategy_chart_exposes_distinct_task_and_handoff_matched_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            rows = [response('a')]
            comparison = dict(model='m', condition='structured', category='ALL',
                              matched_items=18, matched_pairs=54,
                              handoff_matched_items=10, handoff_matched_pairs=30,
                              item_macro_paired_delta=.1, handoff_item_macro_delta=-.2)
            report.build_charts(directory, rows, report.build_summary(rows), [], [comparison])
            root = ET.fromstring((directory / 'charts/strategy_delta.svg').read_text())
            # Inspect visible text nodes: a tooltip or surrounding prose alone
            # cannot disambiguate the denominator of the green handoff bars.
            visible = [node.text for node in root.findall('{http://www.w3.org/2000/svg}text')]
            self.assertIn('Task: 18 matched prompts / 54 matched pairs', visible)
            self.assertIn('Handoff: 10 eligible prompts / 30 matched pairs', visible)


class ReviewCandidates(unittest.TestCase):
    def test_selection_is_deterministic_balanced_and_does_not_edit_labels(self):
        rows = []
        for model in ('model-a', 'model-b', 'model-c'):
            for repeat in range(4):
                rows.append(response(f'{model}-{repeat}-leak', model=model,
                                     forbidden_leak=1, human_pass='', reply='A synthetic canary is quoted.'))
                rows.append(response(f'{model}-{repeat}-confidence', model=model,
                                     high_confidence_error=1, reply='{"answer":0,"confidence":1}'))
        before = copy.deepcopy(rows)
        selected = report.select_review_candidates(rows, maximum=6)
        self.assertEqual(selected, report.select_review_candidates(list(reversed(rows)), maximum=6))
        self.assertEqual(len(selected), 6)
        self.assertEqual(len({row['row_id'] for row in selected}), 6)
        self.assertEqual({row['model'] for row in selected}, {'model-a', 'model-b', 'model-c'})
        self.assertEqual({row['selected_flag'] for row in selected}, {'forbidden-literal signal', 'high-confidence error'})
        self.assertEqual(rows, before)
        self.assertNotIn('human_pass', selected[0])
        self.assertNotIn('auto_pass', selected[0])

    def test_intermediate_truncation_quotes_limited_turn_not_correct_final(self):
        row = response('limited', reply='48', truncated=1,
                       turns=[dict(reply='36. Now I will invent a new task. ' + 'x' * 300, done_reason='length'),
                              dict(reply='48', done_reason='stop')])
        candidate = report.select_review_candidates([row])[0]
        self.assertEqual(candidate['selected_flag'], 'generation limit')
        self.assertEqual(candidate['excerpt_source'], 'assistant turn 1 (generation limit)')
        self.assertTrue(candidate['excerpt'].startswith('36. Now I will invent a new task.'))
        self.assertTrue(candidate['excerpt'].endswith('…'))
        self.assertLessEqual(len(candidate['excerpt']), 201)
        self.assertNotEqual(candidate['excerpt'], row['reply'])

    def test_multiple_signals_keep_one_response_and_retain_all_flags(self):
        row = response('multi', auto_pass=0, content_recoverable_proxy=1,
                       clarification_requested=1, speculative_guess_detected=1, unsupported_action_claim=1)
        candidates = report.select_review_candidates([row])
        self.assertEqual(len(candidates), 1)
        self.assertIn('content proxy', candidates[0]['detected_flags'])
        self.assertIn('clarification plus guessing', candidates[0]['detected_flags'])
        self.assertIn('action-claim', candidates[0]['detected_flags'])

    def test_no_flags_stays_empty_and_is_not_a_safety_claim(self):
        self.assertEqual(report.select_review_candidates([response('clear')]), [])
        section = report.review_candidate_section([])
        self.assertIn('not validated human observations', section)
        self.assertIn('does not establish absence', section)

    def test_main_exports_candidate_schema_and_keeps_human_review_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            row = response('flagged', forbidden_leak=1, reply='A canary quote.')
            (directory / 'scores.jsonl').write_text(json.dumps(row) + '\n')
            report.main(['--run-dir', str(directory)])
            with (directory / 'review_candidates.csv').open() as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, report.REVIEW_CANDIDATE_FIELDS)
                candidates = list(reader)
            self.assertEqual(candidates[0]['row_id'], 'flagged')
            with (directory / 'review.csv').open() as handle:
                self.assertTrue(all(not entry['human_pass'] for entry in csv.DictReader(handle)))
            text = (directory / 'report.md').read_text()
            self.assertIn('A canary quote.', text)
            self.assertIn('problem-enriched sample cannot estimate prevalence', text)
            self.assertIn('No qualitative annotations have been recorded', text)
            self.assertIn('<a href="scores.jsonl">', (directory / 'report.html').read_text())


class TimingTelemetryQuality(unittest.TestCase):
    def test_generation_counter_must_fit_server_total_with_tolerance(self):
        good = response('good', turns=[dict(eval_seconds=1.04, total_duration_seconds=1)])
        bad = response('bad', turns=[dict(eval_seconds=1.06, total_duration_seconds=1)])
        self.assertEqual(report.timing_quality(good)['invalid_eval_telemetry'], 0)
        qa = report.timing_quality(bad)
        self.assertEqual(qa['invalid_eval_telemetry'], 1)
        self.assertEqual(qa['checked_turns'], 1)
        self.assertIn('eval_seconds=1.060000', qa['reason'])

    def test_filter_only_changes_derived_eval_and_throughput(self):
        rows = [response('good', seconds=3, eval_seconds=1, total_duration_seconds=2,
                         tokens_per_second=10, prompt_tokens=10, completion_tokens=10,
                         turns=[dict(eval_seconds=1, total_duration_seconds=2)]),
                response('bad', repeat=2, seconds=2, eval_seconds=1000, total_duration_seconds=1.9,
                         tokens_per_second=.1, prompt_tokens=20, completion_tokens=100,
                         turns=[dict(eval_seconds=1000, total_duration_seconds=1.9)])]
        original = copy.deepcopy(rows)
        summary = report.summarise(rows, 'm', 'baseline', 'ALL')
        self.assertEqual(summary['median_tokens_per_second'], 10)
        self.assertEqual(summary['median_eval_seconds'], 1)
        self.assertEqual(summary['p95_eval_seconds'], 1)
        self.assertEqual(summary['median_seconds'], 2.5)
        self.assertEqual(summary['passes'], 2)
        self.assertEqual(summary['total_completion_tokens'], 110)
        self.assertEqual(summary['timing_checked_conversations'], 2)
        self.assertEqual(summary['timing_inconsistent_conversations'], 1)
        self.assertEqual(summary['timing_inconsistency_rate'], .5)
        self.assertEqual(summary['eval_telemetry_observations'], 1)
        self.assertEqual(summary['throughput_observations'], 1)
        self.assertEqual(summary['eval_telemetry_excluded'], 1)
        tidy = {entry['metric']: entry for entry in report.metric_table([summary], [])}
        self.assertEqual(tidy['median_eval_seconds']['denominator'], 1)
        self.assertEqual(tidy['median_tokens_per_second']['denominator'], 1)
        self.assertEqual(tidy['timing_inconsistency_rate']['denominator'], 2)
        self.assertEqual(rows, original)

    def test_any_bad_intermediate_turn_invalidates_conversation_generation_telemetry(self):
        row = response('partial', eval_seconds=5, tokens_per_second=20,
                       turns=[dict(eval_seconds=100, total_duration_seconds=1),
                              dict(eval_seconds=1, total_duration_seconds=2)])
        qa = report.timing_quality(row)
        self.assertEqual(qa['checked_turns'], 2)
        self.assertEqual(qa['inconsistent_turns'], 1)
        summary = report.summarise([row], 'm', 'baseline', 'ALL')
        self.assertEqual(summary['median_tokens_per_second'], '')
        self.assertEqual(summary['median_eval_seconds'], '')
        self.assertEqual(summary['median_seconds'], 2)

    def test_unavailable_server_counters_are_not_an_observed_zero_failure_rate(self):
        qa = report.timing_quality(response('missing'))
        self.assertEqual(qa['checked_turns'], 0)
        self.assertEqual(qa['invalid_eval_telemetry'], '')
        summary = report.summarise([response('missing')], 'm', 'baseline', 'ALL')
        self.assertEqual(summary['timing_inconsistency_rate'], '')
        self.assertEqual(summary['median_tokens_per_second'], 10)

    def test_main_writes_qa_and_provenance_without_modifying_source_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            row = response('server-bad', eval_seconds=100, total_duration_seconds=1,
                           tokens_per_second=.01, turns=[dict(eval_seconds=100, total_duration_seconds=1)])
            path = directory / 'scores.jsonl'
            path.write_text(json.dumps(row) + '\n')
            original = path.read_bytes()
            report.main(['--run-dir', str(directory)])
            with (directory / 'timing_qa.csv').open() as handle:
                qa = list(csv.DictReader(handle))
            self.assertEqual(qa[0]['invalid_eval_telemetry'], '1')
            provenance = json.loads((directory / 'reporting_provenance.json').read_text())
            self.assertFalse(provenance['raw_records_modified_by_report'])
            self.assertFalse(provenance['behavioral_scores_modified_by_report'])
            self.assertEqual(provenance['tolerance_seconds'], .05)
            self.assertEqual(path.read_bytes(), original)
            self.assertIn('cause of a server counter inconsistency is unestablished', (directory / 'report.md').read_text())


if __name__ == '__main__':
    unittest.main()
