"""Summarise a saved study without contacting Ollama or running any model.

The raw scores stay separate from independent human annotations. All rates are
explicit about their denominator; absent observations are blank, never zero.
"""

import argparse
import csv
import hashlib
import html
import json
import math
import random
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_FIELDS = [
    'model', 'condition', 'category', 'attempts', 'valid_responses', 'errors',
    'error_rate', 'scored', 'passes', 'conservative_pass_rate', 'pass_rate_valid',
    'wilson_low', 'wilson_high', 'objective_scored', 'objective_pass_rate',
    'heuristic_scored', 'heuristic_pass_rate', 'item_macro_pass_rate',
    'repeat_consistency', 'median_seconds', 'p95_seconds', 'mean_seconds',
    'median_ttft_seconds', 'median_tokens_per_second', 'median_words',
    'truncated_count', 'truncation_rate', 'mean_brier_score',
    'mean_reported_confidence', 'calibration_attempts', 'calibration_valid_outputs',
    'calibration_format_rate', 'high_confidence_errors', 'high_confidence_error_rate',
    'clarification_rate', 'uncertainty_rate', 'false_action_claim_rate',
    'refusal_rate', 'forbidden_leak_rate', 'mean_repeated_ngram_ratio',
    'distinct_items', 'distinct_response_ratio', 'repeat_items', 'pass_flip_rate',
    'pass_flip_transitions', 'p95_ttft_seconds', 'median_load_seconds', 'p95_load_seconds',
    'median_prompt_eval_seconds', 'p95_prompt_eval_seconds', 'median_eval_seconds',
    'p95_eval_seconds', 'median_prompt_tokens', 'median_completion_tokens',
    'total_prompt_tokens', 'total_completion_tokens', 'median_chars', 'p95_words',
    'forbidden_leak_observations', 'calibration_ece', 'confidence_accuracy_gap',
    'direct_answer_correct_rate', 'format_contract_pass_rate', 'content_recoverable_proxy_rate',
    'unsupported_action_claim_rate', 'direct_answer_observations', 'format_contract_observations',
    'recoverable_content_observations',
]
ITEM_FIELDS = ['model', 'condition', 'category', 'item_id', 'attempts', 'valid_responses',
               'passes', 'conservative_pass_rate', 'pass_rate_valid', 'repeat_consistency',
               'median_seconds', 'median_words']
COMPARISON_FIELDS = ['model', 'condition', 'reference', 'category', 'matched_pairs',
                     'matched_items', 'condition_pass_rate', 'reference_pass_rate',
                     'paired_delta', 'item_macro_paired_delta', 'wins', 'ties', 'losses',
                     'median_paired_seconds_delta', 'handoff_matched_pairs',
                     'handoff_matched_items', 'handoff_paired_delta', 'handoff_item_macro_delta']
REVIEW_FIELDS = ['row_id', 'rater', 'human_pass', 'notes', 'severity', 'overconfidence',
                 'cognitive_offloading', 'model', 'item_id', 'category', 'condition',
                 'repeat', 'prompt', 'reply', 'rubric']
REVIEW_CANDIDATE_FIELDS = ['row_id', 'model', 'condition', 'item_id', 'category',
                         'selected_flag', 'detected_flags', 'excerpt_source', 'excerpt']
TIMING_QA_FIELDS = ['row_id', 'model', 'condition', 'item_id', 'checked_turns',
                    'inconsistent_turns', 'invalid_eval_telemetry', 'reason']
TIMING_TOLERANCE_SECONDS = .05

# These are reporting-quality flags, not a change to model-behavior scoring.
SUMMARY_FIELDS.extend(['timing_checked_conversations', 'timing_inconsistent_conversations',
                       'timing_inconsistency_rate', 'eval_telemetry_observations',
                       'throughput_observations', 'eval_telemetry_excluded'])


def read_jsonl(path):
    """Read complete JSONL records, failing clearly on malformed/truncated data."""
    if not path.exists():
        return []
    rows = []
    for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f'{path}:{line_no}: invalid JSONL: {exc}') from exc
    return rows


def write_csv(path, fields, rows):
    """Write a header even when no measurements exist."""
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def number(value):
    """Convert finite numeric telemetry, leaving missing values unmeasured."""
    if value in ('', None) or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def values(rows, key):
    """Take only available finite measurements; missing is different from zero."""
    return [value for row in rows if (value := number(row.get(key))) is not None]


def average(data):
    """Arithmetic mean, or a blank when there is no measured denominator."""
    return statistics.mean(data) if data else ''


def median(data):
    """Robust middle value, leaving an absent sample unmeasured."""
    return statistics.median(data) if data else ''


def percentile(data, fraction):
    """Linear interpolated percentile; singleton samples return their value."""
    if not data:
        return ''
    ordered = sorted(data)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def valid(row):
    """A returned visible answer without a provider/transport error."""
    return not row.get('error') and bool(str(row.get('reply', '')).strip())


def passed(row):
    """Conservative success indicator: an error cannot become a pass."""
    return int(valid(row) and str(row.get('auto_pass')) == '1')


def scored(row):
    """Whether an actual returned answer has a binary automatic label."""
    return valid(row) and str(row.get('auto_pass')) in ('0', '1')


def wilson(passes, total):
    """95% Wilson interval; descriptive only because prompt repeats are clustered."""
    if not total:
        return '', ''
    z = 1.959963984540054
    proportion = passes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return max(0, centre - radius), min(1, centre + radius)


def consistency(rows):
    """Majority binary-outcome fraction per item, using only scored replies.

    Requires at least two scored repeats per item. All-fail is as consistent as
    all-pass: this is stability, not quality. Text similarity is not inferred.
    """
    groups = defaultdict(list)
    for row in rows:
        if scored(row):
            groups[row['item_id']].append(int(row['auto_pass']))
    return average([max(sum(group), len(group) - sum(group)) / len(group)
                    for group in groups.values() if len(group) >= 2])


def repeat_diagnostics(rows):
    """Measure text diversity and adjacent-repeat outcome changes separately.

    Repeated responses need not be good responses. Text distinctness preserves
    letter case and punctuation but collapses whitespace. A pass flip is a
    change between adjacent numbered, scored repeats of the same prompt.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[row['item_id']].append(row)
    ratios, flips, transitions = [], 0, 0
    for group in groups.values():
        returned = [row for row in group if valid(row)]
        if len(returned) >= 2:
            ratios.append(len({' '.join(row['reply'].split()) for row in returned}) / len(returned))
        assessed = sorted([row for row in group if scored(row)], key=lambda row: int(row['repeat']))
        for left, right in zip(assessed, assessed[1:]):
            transitions += 1
            flips += int(left['auto_pass']) != int(right['auto_pass'])
    return dict(distinct_response_ratio=average(ratios), repeat_items=len(ratios),
                pass_flip_rate=flips / transitions if transitions else '', pass_flip_transitions=transitions)


def calibration_error(rows):
    """Five-bin empirical ECE on valid explicit probabilities, with no imputation.

    This descriptive statistic can look excellent on a tiny easy task set. It
    always travels with format coverage, number of outputs, and item counts.
    """
    pairs = [(number(row.get('confidence')), number(row.get('answer_correct'))) for row in rows]
    pairs = [(confidence, correct) for confidence, correct in pairs
             if confidence is not None and correct is not None]
    if not pairs:
        return '', ''
    bins = defaultdict(list)
    for confidence, correct in pairs:
        bins[min(int(confidence * 5), 4)].append((confidence, correct))
    ece = sum(len(group) / len(pairs) * abs(average([p for p, _ in group]) - average([y for _, y in group]))
              for group in bins.values())
    return ece, average([p - y for p, y in pairs])


def timing_quality(row):
    """Check that server generation time fits within server total request time.

    A 50 ms tolerance avoids flagging insignificant rounding differences. The
    clocks need not agree with the client clock for this internal server check.
    A bad turn invalidates derived eval/throughput summaries for the whole
    conversation; raw records, token counts and client latency stay untouched.
    Legacy flat records are checked at conversation level when no turns exist.
    """
    turns = row.get('turns') or [row]
    checked, inconsistent, reasons = 0, 0, []
    for index, turn in enumerate(turns, 1):
        evaluation = number(turn.get('eval_seconds'))
        total = number(turn.get('total_duration_seconds'))
        if evaluation is None or total is None:
            continue
        checked += 1
        if evaluation > total + TIMING_TOLERANCE_SECONDS:
            inconsistent += 1
            reasons.append(f'turn {index}: eval_seconds={evaluation:.6f} > '
                           f'total_duration_seconds={total:.6f} + {TIMING_TOLERANCE_SECONDS:.3f}s')
    result = {key: row.get(key, '') for key in ('row_id', 'model', 'condition', 'item_id')}
    result.update(checked_turns=checked, inconsistent_turns=inconsistent,
                  invalid_eval_telemetry=int(bool(inconsistent)) if checked else '',
                  reason='; '.join(reasons) or ('consistent within tolerance' if checked else 'not checked: comparable counters unavailable'))
    return result


def summarise(rows, model, condition, category):
    """Compute one model/strategy/category cell without silently dropping errors."""
    count = len(rows)
    good = [row for row in rows if valid(row)]
    qa = {row['row_id']: timing_quality(row) for row in rows}
    generation_good = [row for row in good if qa[row['row_id']]['invalid_eval_telemetry'] != 1]
    assessable = [row for row in rows if scored(row)]
    successes = sum(passed(row) for row in rows)
    result = dict.fromkeys(SUMMARY_FIELDS, '')
    result.update(model=model, condition=condition, category=category, attempts=count,
                  valid_responses=len(good), errors=sum(bool(row.get('error')) for row in rows),
                  scored=len(assessable), passes=successes,
                  conservative_pass_rate=successes / count if count else '',
                  pass_rate_valid=sum(passed(row) for row in assessable) / len(assessable) if assessable else '',
                  repeat_consistency=consistency(rows))
    result['error_rate'] = result['errors'] / count if count else ''
    result['wilson_low'], result['wilson_high'] = wilson(successes, count)
    for kind in ('objective', 'heuristic'):
        subset = [row for row in assessable if row.get('scoring_kind') == kind]
        result[f'{kind}_scored'] = len(subset)
        result[f'{kind}_pass_rate'] = average([passed(row) for row in subset])
    by_item = defaultdict(list)
    for row in rows:
        by_item[row['item_id']].append(row)
    result['item_macro_pass_rate'] = average([sum(passed(row) for row in group) / len(group)
                                              for group in by_item.values()])
    result['median_seconds'] = median(values(rows, 'seconds'))
    result['p95_seconds'] = percentile(values(rows, 'seconds'), .95)
    result['mean_seconds'] = average(values(rows, 'seconds'))
    result['distinct_items'] = len(by_item)
    result.update(repeat_diagnostics(rows))
    result['median_ttft_seconds'] = median(values(good, 'ttft_seconds'))
    result['median_tokens_per_second'] = median(values(generation_good, 'tokens_per_second'))
    result['median_words'] = median(values(good, 'words'))
    result['truncated_count'] = sum(row.get('truncated') in (1, '1', True) or row.get('done_reason') in ('length', 'max_tokens', 'max_length') for row in rows)
    result['truncation_rate'] = result['truncated_count'] / count if count else ''
    result['mean_brier_score'] = average(values(good, 'brier_score'))
    result['mean_reported_confidence'] = average(values(good, 'confidence'))
    calibration = [row for row in good if number(row.get('brier_score')) is not None]
    calibration_attempts = [row for row in rows if row.get('category') == 'confidence_calibration']
    result['calibration_attempts'] = len(calibration_attempts)
    result['calibration_valid_outputs'] = len(calibration)
    result['calibration_format_rate'] = len(calibration) / len(calibration_attempts) if calibration_attempts else ''
    result['high_confidence_errors'] = sum(int(row.get('high_confidence_error') or 0) for row in calibration) if calibration else ''
    result['high_confidence_error_rate'] = result['high_confidence_errors'] / len(calibration) if calibration else ''
    for output, input_key in [('clarification_rate', 'clarification_detected'),
                               ('uncertainty_rate', 'uncertainty_detected'),
                               ('false_action_claim_rate', 'false_action_claim'),
                               ('refusal_rate', 'refusal_detected'),
                               ('mean_repeated_ngram_ratio', 'repeated_ngram_ratio')]:
        result[output] = average(values(good, input_key))
    # A leakage rate divided by unrelated arithmetic tasks understates failure.
    # Restrict this rate to actual privacy/injection probes, even in ALL rows.
    secret_probes = [row for row in good if row.get('category') in ('privacy', 'prompt_injection')]
    result['forbidden_leak_observations'] = len(values(secret_probes, 'forbidden_leak'))
    result['forbidden_leak_rate'] = average(values(secret_probes, 'forbidden_leak'))
    for key in ('ttft_seconds', 'load_seconds', 'prompt_eval_seconds', 'eval_seconds'):
        measured_rows = generation_good if key == 'eval_seconds' else good
        result['p95_' + key] = percentile(values(measured_rows, key), .95)
        if key != 'ttft_seconds':
            result['median_' + key] = median(values(measured_rows, key))
    for key in ('prompt_tokens', 'completion_tokens'):
        observed = values(good, key)
        result['median_' + key] = median(observed)
        result['total_' + key] = sum(observed) if observed else ''
    result['median_chars'] = median(values(good, 'chars'))
    result['p95_words'] = percentile(values(good, 'words'), .95)
    result['calibration_ece'], result['confidence_accuracy_gap'] = calibration_error(good)
    for output, field, count_key in (
            ('direct_answer_correct_rate', 'direct_answer_correct', 'direct_answer_observations'),
            ('format_contract_pass_rate', 'format_contract_pass', 'format_contract_observations'),
            ('content_recoverable_proxy_rate', 'content_recoverable_proxy', 'recoverable_content_observations')):
        observed = values(good, field)
        result[output], result[count_key] = average(observed), len(observed)
    result['unsupported_action_claim_rate'] = average(values(good, 'unsupported_action_claim'))
    checked = [entry for entry in qa.values() if entry['checked_turns']]
    result['timing_checked_conversations'] = len(checked)
    result['timing_inconsistent_conversations'] = sum(entry['invalid_eval_telemetry'] == 1 for entry in checked)
    result['timing_inconsistency_rate'] = result['timing_inconsistent_conversations'] / len(checked) if checked else ''
    result['eval_telemetry_observations'] = len(values(generation_good, 'eval_seconds'))
    result['throughput_observations'] = len(values(generation_good, 'tokens_per_second'))
    result['eval_telemetry_excluded'] = len(good) - len(generation_good)
    # This internal map is consumed by the tidy export, not serialized as a
    # summary CSV column. It makes each latency/diagnostic denominator exact.
    observations = {key: len(values(rows, 'seconds')) for key in ('median_seconds', 'p95_seconds', 'mean_seconds')}
    for key in ('ttft_seconds', 'load_seconds', 'prompt_eval_seconds', 'eval_seconds',
                'prompt_tokens', 'completion_tokens', 'words', 'chars', 'tokens_per_second'):
        for prefix in ('median_', 'p95_', 'mean_', 'total_'):
            measured_rows = generation_good if key in ('eval_seconds', 'tokens_per_second') else good
            observations[prefix + key] = len(values(measured_rows, key))
    for output, field in (('clarification_rate', 'clarification_detected'), ('uncertainty_rate', 'uncertainty_detected'),
                          ('false_action_claim_rate', 'false_action_claim'), ('refusal_rate', 'refusal_detected'),
                          ('unsupported_action_claim_rate', 'unsupported_action_claim'),
                          ('mean_repeated_ngram_ratio', 'repeated_ngram_ratio')):
        observations[output] = len(values(good, field))
    observations['repeat_consistency'] = sum(sum(scored(row) for row in group) >= 2 for group in by_item.values())
    result['_metric_observation_counts'] = observations
    return result


def build_summary(rows):
    """Group model/condition results by category, plus a clearly scoped ALL row."""
    groups = defaultdict(list)
    for row in rows:
        key = (row['model'], row['condition'])
        groups[key + (row['category'],)].append(row)
        groups[key + ('ALL',)].append(row)
    return [summarise(group, *key) for key, group in sorted(groups.items())]


def build_items(rows):
    """Keep prompt-level evidence available behind each category mean."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row['model'], row['condition'], row['category'], row['item_id'])].append(row)
    results = []
    for (model, condition, category, item_id), group in sorted(groups.items()):
        result = summarise(group, model, condition, category)
        result['item_id'] = item_id
        results.append(result)
    return results


def compare_strategies(rows):
    """Pair the same model/item/repeat; report descriptive deltas against baseline.

    First average repeat deltas within each item for the macro delta. No p-value
    is computed and unmatched cases are not guessed or filled in.
    """
    lookup = {(row['model'], row['item_id'], str(row['repeat']), row['condition']): row for row in rows}
    groups = defaultdict(list)
    for key, row in lookup.items():
        model, item_id, repeat, condition = key
        if condition == 'baseline':
            continue
        reference = lookup.get((model, item_id, repeat, 'baseline'))
        if reference is not None:
            groups[(model, condition, row['category'])].append((row, reference))
            groups[(model, condition, 'ALL')].append((row, reference))
    results = []
    for (model, condition, category), pairs in sorted(groups.items()):
        deltas = [passed(row) - passed(reference) for row, reference in pairs]
        by_item = defaultdict(list)
        for (row, _), delta in zip(pairs, deltas):
            by_item[row['item_id']].append(delta)
        timing = [a - b for row, reference in pairs
                  if (a := number(row.get('seconds'))) is not None
                  and (b := number(reference.get('seconds'))) is not None]
        results.append(dict(model=model, condition=condition, reference='baseline', category=category,
                            matched_pairs=len(pairs), matched_items=len(by_item),
                            condition_pass_rate=average([passed(row) for row, _ in pairs]),
                            reference_pass_rate=average([passed(reference) for _, reference in pairs]),
                            paired_delta=average(deltas), item_macro_paired_delta=average([average(group) for group in by_item.values()]),
                            wins=sum(delta > 0 for delta in deltas), ties=sum(delta == 0 for delta in deltas),
                            losses=sum(delta < 0 for delta in deltas), median_paired_seconds_delta=median(timing)))
        # Compare handoff only where both corresponding rows carry scorer-v2
        # metrics; never reinterpret old outputs with newly improved detectors.
        handoff = [(row, reference) for row, reference in pairs
                   if number(row.get('appropriate_handoff')) is not None
                   and number(reference.get('appropriate_handoff')) is not None]
        item_deltas = defaultdict(list)
        for row, reference in handoff:
            item_deltas[row['item_id']].append(float(row['appropriate_handoff']) - float(reference['appropriate_handoff']))
        results[-1].update(handoff_matched_pairs=len(handoff), handoff_matched_items=len(item_deltas),
                           handoff_paired_delta=average([delta for group in item_deltas.values() for delta in group]),
                           handoff_item_macro_delta=average([average(group) for group in item_deltas.values()]))
    return results


def update_review(path, rows, suite, per_stratum=3):
    """Preserve annotations and add two blinded rater slots per sampled response.

    Sampling is deterministic within model/condition/category, ordered by SHA256
    of row_id. Values of auto_pass are omitted to reduce anchoring. Existing rows
    and any extra columns are retained, including ratings outside the sample.
    """
    existing = []
    fields = list(REVIEW_FIELDS)
    if path.exists():
        with path.open(newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream)
            fields.extend(field for field in reader.fieldnames or [] if field not in fields)
            existing = list(reader)
    keys = {(row.get('row_id'), row.get('rater')) for row in existing}
    by_stratum = defaultdict(list)
    for row in rows:
        by_stratum[(row['model'], row['condition'], row['category'])].append(row)
    for group in by_stratum.values():
        group.sort(key=lambda row: hashlib.sha256(str(row['row_id']).encode()).hexdigest())
        for row in group[:per_stratum]:
            for rater in ('rater_1', 'rater_2'):
                key = (str(row['row_id']), rater)
                if key in keys:
                    continue
                annotation = dict.fromkeys(fields, '')
                annotation.update({field: row.get(field, '') for field in ('row_id', 'model', 'item_id', 'category', 'condition', 'repeat', 'prompt', 'reply')})
                annotation.update(rater=rater, rubric=suite.get(row['item_id'], {}).get('rubric', ''))
                existing.append(annotation)
                keys.add(key)
    write_csv(path, fields, existing)
    return existing


def agreement(annotations, rows):
    """Keep automation validation and independent inter-rater agreement separate."""
    scores = {str(row['row_id']): row for row in rows}
    labels = defaultdict(dict)
    paired = []
    result = {'ratings': 0, 'rated_responses': 0, 'auto_human_agreement': '',
              'objective_auto_human_agreement': '', 'heuristic_auto_human_agreement': '',
              'double_rated': 0, 'inter_rater_agreement': '', 'cohen_kappa': ''}
    kinds = defaultdict(list)
    seen = set()
    for annotation in annotations:
        row_id = str(annotation.get('row_id', ''))
        label = str(annotation.get('human_pass', '')).strip()
        rater = annotation.get('rater', '')
        if label not in ('0', '1') or row_id not in scores:
            continue
        if (row_id, rater) in seen:
            raise ValueError(f'Duplicate human annotation for row_id={row_id}, rater={rater}')
        seen.add((row_id, rater))
        labels[row_id][rater] = int(label)
        row = scores[row_id]
        if scored(row):
            value = int(label) == int(row['auto_pass'])
            paired.append(value)
            kinds[row.get('scoring_kind', '')].append(value)
    result.update(ratings=sum(len(group) for group in labels.values()), rated_responses=len(labels),
                  auto_human_agreement=average(paired))
    for kind in ('objective', 'heuristic'):
        result[f'{kind}_auto_human_agreement'] = average(kinds[kind])
    doubles = [(group['rater_1'], group['rater_2']) for group in labels.values()
               if 'rater_1' in group and 'rater_2' in group]
    result['double_rated'] = len(doubles)
    if doubles:
        observed = average([first == second for first, second in doubles])
        p1 = average([first for first, _ in doubles])
        p2 = average([second for _, second in doubles])
        chance = p1 * p2 + (1 - p1) * (1 - p2)
        result['inter_rater_agreement'] = observed
        result['cohen_kappa'] = (observed - chance) / (1 - chance) if chance < 1 else ''
    return result


def percent(value):
    """Render an observed proportion; keep a missing estimate visibly pending."""
    return f'{float(value):.1%}' if value != '' and value is not None else 'pending'


def decimal(value):
    """Render a numeric metric consistently without turning blank into 0.000."""
    return f'{float(value):.3f}' if value != '' and value is not None else 'pending'


def markdown_table(headers, rows):
    """Escape cell separators so model names and quoted text cannot break tables."""
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(map(cell, headers)) + ' |',
                       '| ' + ' | '.join('---' for _ in headers) + ' |'] +
                      ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows])


def chart_svg(summary):
    """Generate a lightweight shareable SVG; no measurements means no plotted bars."""
    cells = [row for row in summary if row['category'] == 'ALL']
    if not cells:
        return '<p>No chart: the study has not been run.</p>'
    height = 50 + 38 * len(cells)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {height}" role="img" aria-label="Automated pass rate per model and prompt condition">']
    palette = {'baseline': '#2563eb', 'structured': '#0f766e', 'self_check': '#a16207'}
    for index, row in enumerate(cells):
        y = 20 + 38 * index
        full_label = f"{row['model']} / {row['condition']}"
        label = html.escape(full_label if len(full_label) <= 53 else full_label[:50] + '…')
        value = row['conservative_pass_rate']
        parts.extend([f'<text x="5" y="{y + 17}" font-size="12"><title>{html.escape(full_label)}</title>{label}</text>',
                      f'<rect x="420" y="{y}" width="500" height="24" fill="#e2e8f0"/>',
                      f'<rect x="420" y="{y}" width="{500 * value:.2f}" height="24" fill="{palette.get(row["condition"], "#475569")}"/>',
                      f'<text x="930" y="{y + 17}" font-size="12">{value:.1%}</text>'])
    return ''.join(parts) + '</svg>'


def make_report(manifest, rows, summary, comparisons, audit, inventory=None, suite=None, handoff=None, charts=None):
    """Write an honest study draft: completed evidence and still-pending work differ."""
    status = manifest.get('status', 'prepared')
    title = 'NOT RUN — study prepared' if not rows else ('COMPLETE — automated measurements' if status == 'complete' else 'PARTIAL — incomplete run')
    config = manifest.get('config', {})
    planned = manifest.get('planned_jobs', 'not recorded')
    aggregate = [row for row in summary if row['category'] == 'ALL']
    suite = suite or {}
    case_count = len(suite) if suite else len({row['item_id'] for row in rows})
    category_count = len({item['category'] for item in suite.values()}) if suite else len({row['category'] for row in rows})
    parts = [f'# Local LLM interaction study\n\n**Status: {title}.**',
             f"Recorded attempts: **{len(rows)}**. Planned attempts: **{planned}**. Manifest status: `{status}`. "
             f"Planned inference requests (including multi-turn cases): **{manifest.get('planned_requests', 'not recorded')}**. "
             f"Configured conditions: `{', '.join(config.get('conditions', [])) or 'not recorded'}`. "
             'No measurement is inferred for a model, item, or condition that has not run.',
             '## 1. Define\n\n**When a request is unclear, or cannot be answered, does the model hand control '
             'back to the human, or does it guess on our behalf?** '
             'Good interaction means direct answers when the task is clear; clarification when necessary; '
             'honest uncertainty; compliance with output and action constraints; protection of synthetic secrets; '
             'use of corrections and context; evidence-grounded answers; and refusal of nonconsensual privacy abuse. '
             'Speed and verbosity are tradeoffs, not substitutes for correctness.',
             f'The frozen study snapshot contains {case_count} cases across {category_count} categories. Objective exact/JSON/regex/calibration checks and heuristic '
             'behavior screens remain separate in the CSVs. A heuristic pass is a signal for human review, '
             'not proof of safety, security, or good usability.',
             '## 2. Design\n\nA within-item design compares `baseline`, `structured`, and `self_check` instructions. '
             'The same item/repeat seed is paired across conditions; fresh conversations separate cases. '
             'Memory and correction cases retain only their own conversation history. '
             'All installed local chat-capable model tags are inventoried; skips and errors must be reported. '
             'No new models are downloaded. Detailed protocol: [PROTOCOL.md](../../docs/PROTOCOL.md).',
             f"Configured repetitions: `{config.get('repeats', 'pending')}`; temperature: `{config.get('temperature', 'pending')}`; "
             f"token cap: `{config.get('num_predict', 'pending')}`; seed: `{config.get('seed', 'pending')}`. "
             'These settings and the prompt snapshot must be frozen before the first inference request.',
             '## 3. Implement\n\n`run_tests.py` records raw replies, conversation turns, status, latency, '
             'time to first answer token, server load/evaluation times, and token counts. `score.py` applies '
             'auditable deterministic checks. `report.py` summarises saved scores and creates review forms. '
             'The group must trace and challenge the scoring functions using the offline unit tests before '
             'interpreting scores. See [SCORING_GUIDE.md](../../docs/SCORING_GUIDE.md).',
             '## 4. Run\n\n' + ('**No model has been evaluated.** Connect power and explicitly start the pipeline when ready.' if not rows else
             'The tables below contain the recorded attempts only. Inspect the manifest for completion status, skips, model digests, order, environment, and generation settings.'),
             '## 5. Score\n\n' + ('All result CSVs currently contain headers only. Quantitative findings, comparisons, and qualitative observations are pending.' if not rows else
             'The table mixes categories only as a descriptive overview; use `summary.csv` for category-level and objective-versus-heuristic results. Conservative pass rate counts every recorded attempt, including errors, in its denominator. Conditional pass rate includes valid, scored replies only.')]
    known_models = (inventory or {}).get('models', []) or manifest.get('models', [])
    if known_models:
        digest_names = defaultdict(list)
        for model in known_models:
            digest_names[model.get('digest', model.get('name', ''))].append(model.get('name', ''))
        selected = {model.get('name') for model in manifest.get('models', [])}
        model_rows = []
        for model in known_models:
            details = model.get('details') or {}
            size = number(model.get('size'))
            aliases = [name for name in digest_names[model.get('digest', model.get('name', ''))] if name != model.get('name')]
            model_rows.append([model.get('name', ''), str(model.get('digest', ''))[:12], details.get('parameter_size', ''),
                               details.get('quantization_level', ''), f'{size / 1e9:.2f} GB' if size is not None else 'unknown',
                               ', '.join(aliases) or 'none', 'planned' if model.get('name') in selected else model.get('skip_reason') or 'alias / not selected'])
        parts.insert(2, '## Model inventory\n\nInventory metadata describes installed configurations; response metrics appear below when available. '
                     'Full names identify the installed configurations. Digest aliases share weights and are not independent models.\n\n' +
                     markdown_table(['Installed tag', 'Digest prefix', 'Parameters', 'Quantisation', 'Disk size', 'Same-digest aliases', 'Plan / skip reason'], model_rows))
    parts.insert(3, research_section(handoff or []))
    if aggregate:
        table = [[row['model'], row['condition'], row['attempts'], row['errors'], percent(row['conservative_pass_rate']),
                  percent(row['pass_rate_valid']), f"{percent(row['wilson_low'])}–{percent(row['wilson_high'])}",
                  decimal(row['median_seconds']), decimal(row['p95_seconds']), decimal(row['median_tokens_per_second'])]
                 for row in aggregate]
        parts.append(markdown_table(['Model', 'Condition', 'Attempts', 'Errors', 'Pass / all', 'Pass / valid scored',
                                     '95% Wilson', 'Median s', 'p95 s', 'Median token/s'], table))
        parts.append('### Observed scope by condition\n\n' + markdown_table(
            ['Model', 'Condition', 'Distinct prompts', 'Observed attempts'],
            [[row['model'], row['condition'], row['distinct_items'], row['attempts']] for row in aggregate]) +
            '\n\nIf a broad baseline run is combined with a focused strategy experiment, the overall bars have different '
            'task scopes and must not be read as causal strategy effects. Use the matched-prompt comparison table below.')
    if charts:
        parts.append('## Automatically generated graphs\n\nEvery graph is exported as a standalone SVG in `charts/`. '
                     'Missing measurements are marked unavailable and never plotted as zero. The tidy source values and '
                     'denominators are in `metric_summary.csv`; primary endpoints are in `handoff_summary.csv`.')
        for chart in charts:
            parts.append(f"### {chart['title']}\n\n![{chart['title']}]({chart['file']})")
    if comparisons:
        parts.append('### Prompt strategy comparisons\n\nPaired observations use the same model, item, and repeat. '
                     'A positive delta favours the named condition over baseline. The item macro delta averages '
                     'repeats within each prompt before averaging prompts; it is descriptive and has no significance claim.')
        parts.append(markdown_table(['Model', 'Condition', 'Matched pairs', 'Items', 'Task macro delta',
                                     'Handoff matched pairs', 'Handoff macro delta', 'Median extra seconds'],
                     [[row['model'], row['condition'], row['matched_pairs'], row['matched_items'],
                       percent(row['item_macro_paired_delta']), row['handoff_matched_pairs'],
                       percent(row['handoff_item_macro_delta']), decimal(row['median_paired_seconds_delta'])]
                      for row in comparisons if row['category'] == 'ALL']))
    calibration_summary = [row for row in summary if row['category'] == 'confidence_calibration']
    if calibration_summary:
        parts.append('### Confidence coverage\n\nBrier scores and high-confidence error rates use valid confidence outputs only. '
                     'Read the format rate alongside them: selective missing or malformed outputs can make conditional Brier loss misleading. '
                     'High-confidence error rate means wrong answers with confidence ≥0.8 divided by all valid confidence outputs.\n\n' +
                     markdown_table(['Model', 'Condition', 'Attempts', 'Valid confidence outputs', 'Format rate', 'Mean Brier', 'High-confidence error rate'],
                     [[row['model'], row['condition'], row['calibration_attempts'], row['calibration_valid_outputs'],
                       percent(row['calibration_format_rate']), decimal(row['mean_brier_score']), percent(row['high_confidence_error_rate'])]
                      for row in calibration_summary]))
    parts.append(timing_quality_section(rows, aggregate))
    parts.append(review_candidate_section(select_review_candidates(rows)))
    parts.extend(['### Measurement interpretation\n\nWilson intervals describe observed binary outcomes only. '
                  'Repeats of the same prompt are correlated, the prompts are a purposive convenience sample, '
                  'and the intervals do not establish general model superiority. Report per-item macro rates and '
                  'outcome consistency alongside them. Consistency means agreement of binary pass/fail repeats; '
                  'an always-wrong model can be perfectly consistent. Token throughput is derived from Ollama '
                  'generation time; wall latency includes overhead and loading. No energy, battery, RAM, GPU, '
                  'or end-user ease-of-use measurement is claimed.',
                  'Confidence items cover a small synthetic question set. Brier score is the mean of '
                  '`(reported probability correct − observed correctness)²`; lower is better. '
                  'Malformed calibration JSON is an automated failure, with no invented probability. '
                  'These few questions cannot establish real-world calibration.',
                  '### Independent human review\n\nReviewers should read the item rubric and reply without '
                  'consulting the automatic label first. `review.csv` contains up to three deterministically '
                  'sampled responses per model/condition/category and two independent rater slots; existing '
                  'ratings survive regeneration. Add extra error, truncation, secret-leak, or disagreement cases '
                  'as a separate diagnostic sample and identify that sampling choice in the final write-up. '
                  'Human labels validate the scorer; they never replace automated rates in a partly reviewed dataset.',
                  f"Completed human ratings: **{audit['ratings']}** across **{audit['rated_responses']}** replies. "
                  f"Auto/human agreement: **{percent(audit['auto_human_agreement'])}**; "
                  f"objective: **{percent(audit['objective_auto_human_agreement'])}**; "
                  f"heuristic: **{percent(audit['heuristic_auto_human_agreement'])}**. "
                  f"Replies double-rated by rater_1/rater_2: **{audit['double_rated']}**; "
                  f"inter-rater agreement: **{percent(audit['inter_rater_agreement'])}**; "
                  f"Cohen's kappa: **{decimal(audit['cohen_kappa'])}** (undefined when chance agreement is 1).",
                  '### Independent qualitative observations — human worksheet\n\n'
                  '| Response ID / model | Exact observed behaviour or short quote | Human impact | Rater interpretation / alternative explanation |\n'
                  '| --- | --- | --- | --- |\n'
                  '| Pending | No qualitative annotations have been recorded in this report yet. | Pending | Pending |\n\n'
                  'Look for overconfidence, refusal followed by enabling advice, clarification followed by guessing, '
                  'loops or repetition, fabricated completed actions, and failure to retain a correction. '
                  'Do not equate a repeated-n-gram diagnostic with a loop without reading the reply. '
                  'Past group experiences may be included as clearly labelled anecdotes with context and date; '
                  'they are not repeated experimental trials.',
                  '## Cognitive offloading reflection\n\nThe intended delegation is drafting prompts, writing '
                  'boilerplate, recording repeated interactions, computing deterministic metrics, and assembling '
                  'tables. Humans retain the research question, operational definitions, rubric validity, '
                  'understanding of every scoring function, independent reading of outputs, and final conclusions. '
                  'Because AI helped prepare this pipeline, the group must record what it accepted, changed, '
                  'and rejected. The likely sweet spot is automated collection plus auditable scoring and '
                  'human interpretation; that is a proposed workflow, not an observed result.\n\n'
                  'Complete after the study: Which task saved time? Which delegated judgment needed correction? '
                  'Did checking the AI output cost more than doing the task directly? Where did familiarity '
                  'with the code improve or reduce trust? What evidence changed the group’s view?',
                  '## Group contributions\n\nFor a group of six: (1) research question and metric definitions; '
                  '(2) prompt construction and protocol; (3) runner/provider implementation; (4) scoring and '
                  'adversarial unit tests; (5) independent qualitative review; (6) statistical interpretation '
                  'and report editing. For five, combine protocol and runner roles. Everyone reads the core '
                  'scorer; at least two people independently rate the selected outputs. Fill in names and '
                  'actual contributions after the work.',
                  '## Limits and threats to validity\n\nThis is a small English-language synthetic suite on '
                  'one machine. Model size, quantisation, templates, context limits, thinking defaults, and '
                  'supported capabilities differ. Serial execution and changing thermal conditions can affect '
                  'speed; the load-duration column helps distinguish loading from generation. No external '
                  'tools execute actions, so human-control cases measure claims and textual constraint '
                  'following. Secret tests use synthetic canaries and do not establish protection against '
                  'arbitrary attacks. The self-check condition changes instructions, not hidden reasoning '
                  'quality. No usability study with recruited participants, causal cognitive-offloading '
                  'effect, or population-wide safety claim is supported. Changes after examining results '
                  'must be logged as exploratory and rerun in a new study directory.'])
    return '\n\n'.join(parts) + '\n'


def markdown_html(source):
    """Render the report's small Markdown subset without external dependencies.

    Source text is escaped before inline formatting. Only HTTPS links and the
    report's relative documentation/chart/source links become anchors; no raw HTML executes.
    """
    def inline(value):
        value = html.escape(value)
        value = re.sub(r'`([^`]+)`', r'<code>\1</code>', value)
        value = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', value)
        return re.sub(r'\[([^\]]+)\]\(((?:(?:https://|\.\./|charts/)[^\s)]+|(?:scores|responses)\.jsonl|(?:review_candidates|timing_qa)\.csv))\)',
                      r'<a href="\2">\1</a>', value)

    output, paragraph = [], []
    lines = source.splitlines()
    def flush():
        if paragraph:
            output.append('<p>' + inline(' '.join(paragraph)) + '</p>')
            paragraph.clear()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            flush()
        elif line.startswith('#'):
            flush()
            match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if match:
                level = len(match[1])
                output.append(f'<h{level}>{inline(match[2])}</h{level}>')
            else:
                paragraph.append(line)
        elif re.fullmatch(r'!\[[^\]]*\]\(charts/[A-Za-z0-9_.-]+\.svg\)', line):
            flush()
            match = re.fullmatch(r'!\[([^\]]*)\]\((charts/[A-Za-z0-9_.-]+\.svg)\)', line)
            output.append(f'<figure class="chart"><img src="{html.escape(match[2], quote=True)}" '
                          f'alt="{html.escape(match[1], quote=True)}" loading="lazy"></figure>')
        elif line.startswith('| ') and index + 1 < len(lines) and re.match(r'^\|\s*---', lines[index + 1]):
            flush()
            def cells(value):
                return [part.strip().replace('\\|', '|') for part in re.split(r'(?<!\\)\|', value.strip().strip('|'))]
            output.append('<div class="table-scroll"><table><thead><tr>' + ''.join('<th>' + inline(cell) + '</th>' for cell in cells(line)) + '</tr></thead><tbody>')
            index += 2
            while index < len(lines) and lines[index].startswith('| '):
                output.append('<tr>' + ''.join('<td>' + inline(cell) + '</td>' for cell in cells(lines[index])) + '</tr>')
                index += 1
            output.append('</tbody></table></div>')
            continue
        else:
            paragraph.append(line)
        index += 1
    flush()
    return '\n'.join(output)


HANDOFF_FIELDS = [
    'model', 'condition', 'primary_attempts', 'primary_items', 'primary_metric_observations',
    'instrumented_primary_attempts',
    'primary_metric_coverage', 'handoff_passes', 'handoff_rate_all', 'handoff_rate_valid',
    'handoff_item_macro', 'handoff_cluster_low', 'handoff_cluster_high',
    'guess_observations', 'guess_count', 'guess_rate', 'clarification_observations',
    'clarification_rate', 'mean_clarification_specificity', 'uncertainty_observations',
    'uncertainty_rate', 'control_attempts', 'control_observations', 'unnecessary_clarification_count',
    'overclarification_rate', 'direct_answer_observations', 'direct_answer_rate',
    'resolution_attempts', 'resolution_observations', 'resolution_rate',
]
METRIC_FIELDS = ['model', 'condition', 'category', 'metric', 'value', 'denominator', 'denominator_definition']


def cluster_interval(item_rates, samples=1000, seed=20261021):
    """Percentile interval by resampling prompt-level rates, not reply-level rows.

    Each prompt receives equal weight after its repetitions are averaged. The
    interval describes sensitivity to this purposive prompt set; it is neither
    a population guarantee nor a significance test. Fewer than two prompts has
    no defensible prompt-resampling interval, so both endpoints remain blank.
    """
    if len(item_rates) < 2:
        return '', ''
    rng = random.Random(seed)
    bootstraps = [statistics.mean(rng.choices(item_rates, k=len(item_rates))) for _ in range(samples)]
    return percentile(bootstraps, .025), percentile(bootstraps, .975)


def build_handoff(rows):
    """Answer the main question with eligible scopes and explicit missingness.

    Handoff success uses ambiguous or unanswerable items only. An operational
    error is a failure in the all-attempt rate but remains absent in conditional
    behavior rates. Missing v2 fields on old data never become measured zeros.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[(row['model'], row['condition'])].append(row)
    output = []
    for (model, condition), group in sorted(groups.items()):
        result = dict.fromkeys(HANDOFF_FIELDS, '')
        primary = [row for row in group if row.get('research_role') in ('needs_clarification', 'unanswerable')]
        clarifying = [row for row in primary if row.get('research_role') == 'needs_clarification']
        unknown = [row for row in primary if row.get('research_role') == 'unanswerable']
        controls = [row for row in group if row.get('research_role') == 'answerable_control']
        resolved = [row for row in group if row.get('research_role') == 'resolved_followup']
        observed = [row for row in primary if valid(row) and number(row.get('appropriate_handoff')) is not None]
        # A row must belong to the v2 instrument before its missing metric can
        # enter a conservative failure denominator. Legacy rows stay pending.
        instrumented = [row for row in primary if 'appropriate_handoff' in row]
        success = sum(float(row['appropriate_handoff']) for row in observed)
        by_item = defaultdict(list)
        for row in instrumented:
            by_item[row['item_id']].append(float(row['appropriate_handoff'])
                                          if valid(row) and number(row.get('appropriate_handoff')) is not None else 0)
        item_rates = [average(repeats) for repeats in by_item.values()]
        result.update(model=model, condition=condition, primary_attempts=len(primary),
                      instrumented_primary_attempts=len(instrumented),
                      primary_items=len({row['item_id'] for row in primary}),
                      primary_metric_observations=len(observed),
                      primary_metric_coverage=len(observed) / len(primary) if primary else '',
                      handoff_passes=success if instrumented else '',
                      handoff_rate_all=success / len(instrumented) if instrumented else '',
                      handoff_rate_valid=success / len(observed) if observed else '',
                      handoff_item_macro=average(item_rates),
                      control_attempts=len(controls), resolution_attempts=len(resolved))
        result['handoff_cluster_low'], result['handoff_cluster_high'] = cluster_interval(item_rates)
        for subset, key, prefix in ((primary, 'speculative_guess_detected', 'guess'),
                                    (clarifying, 'clarification_requested', 'clarification'),
                                    (unknown, 'uncertainty_acknowledged', 'uncertainty'),
                                    (controls, 'unnecessary_clarification', 'overclarification'),
                                    (controls, 'direct_answer_correct', 'direct_answer'),
                                    (resolved, 'resolution_success', 'resolution')):
            measurements = values([row for row in subset if valid(row)], key)
            count_key = 'control_observations' if prefix == 'overclarification' else prefix + '_observations'
            result[count_key] = len(measurements)
            result[prefix + '_rate'] = average(measurements)
            if prefix == 'guess':
                result['guess_count'] = sum(measurements) if measurements else ''
            if prefix == 'overclarification':
                result['unnecessary_clarification_count'] = sum(measurements) if measurements else ''
        result['mean_clarification_specificity'] = average(values([row for row in clarifying if valid(row)], 'clarification_specificity'))
        output.append(result)
    return output


def metric_table(summary, handoff):
    """Export a tidy table: one metric per row with a named denominator.

    Counts and missing measurements remain explicit rather than assigning one
    universal sample size to quality, latency, confidence and human-control data.
    """
    output = []
    for row in summary:
        for field in SUMMARY_FIELDS:
            if field in ('model', 'condition', 'category'):
                continue
            if field in ('pass_rate_valid', 'objective_pass_rate', 'heuristic_pass_rate'):
                denominator = row[{'pass_rate_valid': 'scored', 'objective_pass_rate': 'objective_scored',
                                   'heuristic_pass_rate': 'heuristic_scored'}[field]]
                definition = 'valid scored replies in the stated scoring kind'
            elif field == 'calibration_format_rate':
                denominator, definition = row['calibration_attempts'], 'all recorded calibration attempts'
            elif field in ('calibration_ece', 'mean_brier_score', 'mean_reported_confidence', 'high_confidence_error_rate', 'confidence_accuracy_gap'):
                denominator, definition = row['calibration_valid_outputs'], 'valid explicit confidence outputs; consult calibration_format_rate'
            elif field == 'forbidden_leak_rate':
                denominator, definition = row['forbidden_leak_observations'], 'returned privacy/injection probe replies only'
            elif field == 'timing_inconsistency_rate':
                denominator, definition = row['timing_checked_conversations'], 'conversations with comparable server eval/total counters'
            elif field in ('direct_answer_correct_rate', 'format_contract_pass_rate', 'content_recoverable_proxy_rate'):
                denominator = row[{'direct_answer_correct_rate': 'direct_answer_observations',
                                   'format_contract_pass_rate': 'format_contract_observations',
                                   'content_recoverable_proxy_rate': 'recoverable_content_observations'}[field]]
                definition = 'returned eligible replies with the stated scorer-v2 measurement'
            elif field == 'pass_flip_rate':
                denominator, definition = row['pass_flip_transitions'], 'adjacent assessed repeat pairs within each prompt'
            elif field in ('item_macro_pass_rate', 'repeat_consistency'):
                denominator, definition = row['distinct_items'], 'distinct observed prompts; repeat metrics require at least two returned/scored repeats'
            elif field == 'distinct_response_ratio':
                denominator, definition = row['repeat_items'], 'prompts with at least two valid returned replies'
            elif field.endswith('_rate') or field in ('wilson_low', 'wilson_high'):
                denominator, definition = row['attempts'], 'all recorded attempts'
            elif field.startswith(('median_', 'mean_', 'p95_')):
                denominator, definition = '', 'available finite observations of this metric; missing telemetry excluded'
            else:
                denominator, definition = '', 'count or descriptive statistic; no ratio denominator'
            if field in row.get('_metric_observation_counts', {}):
                denominator = row['_metric_observation_counts'][field]
                definition = 'available finite observations; missing telemetry or diagnostics excluded'
            output.append(dict(model=row['model'], condition=row['condition'], category=row['category'],
                               metric=field, value=row.get(field, ''), denominator=denominator,
                               denominator_definition=definition))
    for row in handoff:
        for field in HANDOFF_FIELDS:
            if field in ('model', 'condition'):
                continue
            denominator, definition = row['primary_attempts'], 'attempted ambiguous/unanswerable cases'
            if field == 'handoff_rate_all':
                denominator, definition = row['instrumented_primary_attempts'], 'all instrumented eligible attempts, including errors as failures'
            if field == 'handoff_rate_valid':
                denominator, definition = row['primary_metric_observations'], 'returned eligible replies with an observed v2 handoff metric'
            if field in ('handoff_item_macro', 'handoff_cluster_low', 'handoff_cluster_high'):
                denominator, definition = row['primary_items'], 'distinct eligible prompts after within-prompt averaging'
            if field == 'guess_rate':
                denominator, definition = row['guess_observations'], 'returned eligible replies with an observed guessing metric'
            if field == 'overclarification_rate':
                denominator, definition = row['control_observations'], 'returned answerable controls with an observed clarification metric'
            if field == 'resolution_rate':
                denominator, definition = row['resolution_observations'], 'returned final replies to resolved-followup cases'
            if field == 'clarification_rate':
                denominator, definition = row['clarification_observations'], 'returned needs-clarification replies with an observed request metric'
            if field == 'uncertainty_rate':
                denominator, definition = row['uncertainty_observations'], 'returned unanswerable replies with an observed uncertainty metric'
            if field == 'direct_answer_rate':
                denominator, definition = row['direct_answer_observations'], 'returned answerable controls with an observed correctness metric'
            if not field.endswith(('_rate', '_all', '_valid', '_coverage', '_macro', '_low', '_high')):
                denominator, definition = '', 'count or descriptive statistic; no ratio denominator'
            output.append(dict(model=row['model'], condition=row['condition'], category='RESEARCH_ROLES',
                               metric=field, value=row.get(field, ''), denominator=denominator,
                               denominator_definition=definition))
    return output


def svg_label(value, limit=44):
    """Clip labels visually but retain their entire text in an SVG title."""
    value = str(value)
    short = value if len(value) <= limit else value[:limit - 1] + '…'
    return f'<title>{html.escape(value)}</title>{html.escape(short)}'


def svg_bars(title, subtitle, rows, series, *, percent_axis=False, signed=False):
    """Draw a self-contained horizontal chart with one shared unit and scale.

    Each series is (field, label, color). Missing observations draw no bar and
    say 'pending'; they never become zero. Every chart exports usable SVG text.
    """
    width, label_width, bar_width = 1200, 390, 660
    group_height = 22 * len(series) + 19
    height = 120 + len(rows) * group_height
    observed = [abs(value) for row in rows for field, _, _ in series if (value := number(row.get(field))) is not None]
    scale = 1.0 if percent_axis else max(observed, default=1.0) * 1.08 or 1.0
    origin = label_width + (bar_width / 2 if signed else 0)
    span = bar_width / 2 if signed else bar_width
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title, quote=True)}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="18" y="26" font-family="system-ui,sans-serif" font-size="20" fill="#172033">{html.escape(title)}</text>',
             f'<text x="18" y="49" font-family="system-ui,sans-serif" font-size="12" fill="#526174">{html.escape(subtitle)}</text>']
    for index, (_, label, color) in enumerate(series):
        x = 18 + index * (1150 / max(len(series), 1))
        parts.extend([f'<rect x="{x}" y="64" width="12" height="12" fill="{color}"/>',
                      f'<text x="{x + 18}" y="75" font-family="system-ui,sans-serif" font-size="12">{html.escape(label)}</text>'])
    for index, row in enumerate(rows):
        y = 102 + index * group_height
        label = f"{row['model']} / {row['condition']}"
        parts.append(f'<text x="18" y="{y + 15}" font-family="system-ui,sans-serif" font-size="12">{svg_label(label)}</text>')
        # Scope is visible on the plot itself, not just in a distant CSV. A full
        # baseline and a focused ablation do not have interchangeable task sets.
        items = row.get('distinct_items', row.get('primary_items', row.get('matched_items')))
        attempts = row.get('attempts', row.get('primary_attempts', row.get('matched_pairs')))
        if items is not None:
            paired_scope = 'matched_items' in row and 'distinct_items' not in row
            noun = 'matched prompts' if paired_scope else 'eligible prompts' if 'primary_items' in row else 'prompts'
            count_noun = 'matched pairs' if paired_scope else 'attempts'
            detail = f'{items} {noun}' + (f' / {attempts} {count_noun}' if attempts is not None else '')
            if paired_scope and 'handoff_matched_items' in row:
                # The paired task set also contains answerable controls. The
                # handoff series uses only matched unclear/unanswerable items,
                # so give it its own visible scope rather than reusing 18/54.
                detail = 'Task: ' + detail
                handoff_detail = (f"Handoff: {row['handoff_matched_items']} eligible prompts / "
                                  f"{row['handoff_matched_pairs']} matched pairs")
                parts.append(f'<text x="18" y="{y + 46}" font-family="system-ui,sans-serif" font-size="10" fill="#64748b">{html.escape(handoff_detail)}</text>')
            parts.append(f'<text x="18" y="{y + 31}" font-family="system-ui,sans-serif" font-size="10" fill="#64748b">{html.escape(detail)}</text>')
        if signed:
            parts.append(f'<line x1="{origin}" x2="{origin}" y1="{y - 4}" y2="{y + group_height - 12}" stroke="#64748b"/>')
        for offset, (field, _, color) in enumerate(series):
            line_y = y + 22 * offset
            value = number(row.get(field))
            if value is None:
                parts.append(f'<text x="{origin + 6}" y="{line_y + 14}" font-size="11" fill="#64748b">pending</text>')
                continue
            length = abs(value) / scale * span
            x = origin - length if value < 0 else origin
            parts.append(f'<rect x="{x:.2f}" y="{line_y}" width="{length:.2f}" height="17" rx="2" fill="{color}"/>')
            printed = f'{value:+.1%}' if signed and percent_axis else f'{value:.1%}' if percent_axis else f'{value:.2f}'
            parts.append(f'<text x="{label_width + bar_width + 12}" y="{line_y + 13}" font-family="system-ui,sans-serif" font-size="11">{printed}</text>')
    return ''.join(parts) + '</svg>'


def heatmap_svg(summary):
    """Plot observed category pass rates; grey cells mean no recorded attempts."""
    groups = sorted({(row['model'], row['condition']) for row in summary})
    categories = sorted({row['category'] for row in summary if row['category'] != 'ALL'})
    lookup = {(row['model'], row['condition'], row['category']): row for row in summary}
    width, height = 390 + 105 * len(categories), 140 + 38 * len(groups)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Pass rates by task category">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="18" y="26" font-size="20" font-family="system-ui,sans-serif">Category pass rates</text>',
             '<text x="18" y="48" font-size="12" font-family="system-ui,sans-serif" fill="#526174">Observed passes / all recorded attempts; grey = unavailable. Category scopes differ; review counts in summary.csv.</text>']
    for column, category in enumerate(categories):
        words = category.replace('_', ' ').split()
        midpoint = max(1, len(words) // 2)
        lines = [' '.join(words[:midpoint]), ' '.join(words[midpoint:])]
        x = 390 + column * 105
        for offset, line in enumerate(lines):
            parts.append(f'<text x="{x + 3}" y="{80 + 15 * offset}" font-size="10" font-family="system-ui,sans-serif">{html.escape(line)}</text>')
    for index, (model, condition) in enumerate(groups):
        y = 112 + index * 38
        parts.append(f'<text x="18" y="{y + 21}" font-size="12" font-family="system-ui,sans-serif">{svg_label(model + " / " + condition)}</text>')
        for column, category in enumerate(categories):
            cell = lookup.get((model, condition, category))
            value = number(cell.get('conservative_pass_rate')) if cell else None
            x = 390 + column * 105
            fill = '#e2e8f0' if value is None else f'rgb({round(244 - 214 * value)},{round(248 - 128 * value)},{round(251 - 96 * value)})'
            printed = '—' if value is None else f'{value:.0%}'
            text_color = 'white' if value is not None and value >= .6 else '#172033'
            detail = f'{model} / {condition} / {category}: ' + ('unavailable' if cell is None else f"{cell['passes']}/{cell['attempts']} passes")
            parts.extend([f'<rect x="{x}" y="{y}" width="100" height="31" rx="2" fill="{fill}"><title>{html.escape(detail)}</title></rect>',
                          f'<text x="{x + 50}" y="{y + 21}" text-anchor="middle" font-size="12" font-family="system-ui,sans-serif" fill="{text_color}">{printed}</text>'])
    return ''.join(parts) + '</svg>'


def scatter_svg(summary):
    """Compare throughput and verbosity on separate axes, never a combined score."""
    rows = [row for row in summary if row['category'] == 'ALL' and number(row.get('median_tokens_per_second')) is not None and number(row.get('median_words')) is not None]
    if not rows:
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 120" role="img" '
                'aria-label="Throughput telemetry unavailable"><rect width="100%" height="100%" fill="white"/>'
                '<text x="18" y="40" font-size="20" font-family="system-ui,sans-serif">Throughput and verbosity</text>'
                '<text x="18" y="78" font-size="14" font-family="system-ui,sans-serif">No finite throughput and word-count measurements are available.</text></svg>')
    width, height = 1200, 510 + len(rows) * 21
    x_max = max([float(row['median_tokens_per_second']) for row in rows] or [1]) * 1.15 or 1
    y_max = max([float(row['median_words']) for row in rows] or [1]) * 1.15 or 1
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Generation throughput and response length">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="18" y="26" font-size="20" font-family="system-ui,sans-serif">Throughput and verbosity</text>',
             '<text x="18" y="49" font-size="12" font-family="system-ui,sans-serif" fill="#526174">Median generation token/s vs median final-answer words; tokens may include thinking and tokenizers differ.</text>',
             '<line x1="100" y1="420" x2="1110" y2="420" stroke="#64748b"/>',
             '<line x1="100" y1="80" x2="100" y2="420" stroke="#64748b"/>',
             '<text x="530" y="460" font-size="13" font-family="system-ui,sans-serif">Generation tokens per second →</text>',
             '<text x="18" y="72" font-size="12" font-family="system-ui,sans-serif">Words ↑</text>']
    for tick in range(6):
        x, y = 100 + tick * 202, 420 - tick * 68
        parts.extend([f'<text x="{x}" y="440" text-anchor="middle" font-size="11">{x_max * tick / 5:.1f}</text>',
                      f'<text x="88" y="{y + 4}" text-anchor="end" font-size="11">{y_max * tick / 5:.0f}</text>',
                      f'<line x1="100" x2="1110" y1="{y}" y2="{y}" stroke="#e2e8f0"/>'])
    for index, row in enumerate(rows, 1):
        x = 100 + float(row['median_tokens_per_second']) / x_max * 1010
        y = 420 - float(row['median_words']) / y_max * 340
        full_label = f"{index}. {row['model']} / {row['condition']}"
        parts.extend([f'<circle cx="{x:.2f}" cy="{y:.2f}" r="8" fill="#0f766e"><title>{html.escape(full_label)}</title></circle>',
                      f'<text x="{x + 11:.2f}" y="{y + 4:.2f}" font-size="12">{index}</text>',
                      f'<text x="30" y="{492 + 21 * (index - 1)}" font-size="12" font-family="system-ui,sans-serif">{html.escape(full_label)}</text>'])
    return ''.join(parts) + '</svg>'


def reliability_svg(rows):
    """Exploratory reliability bins only when enough explicit probabilities exist.

    No output is generated below 10 valid observations / 3 independent prompts.
    Those thresholds are display guards, not statistical validation thresholds.
    """
    groups = defaultdict(list)
    for row in rows:
        if valid(row) and number(row.get('confidence')) is not None and number(row.get('answer_correct')) is not None:
            groups[(row['model'], row['condition'])].append(row)
    eligible = {key: group for key, group in groups.items() if len(group) >= 10 and len({row['item_id'] for row in group}) >= 3}
    if not eligible:
        return None
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 610" role="img" aria-label="Exploratory confidence reliability bins">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="18" y="26" font-size="20" font-family="system-ui,sans-serif">Exploratory confidence reliability</text>',
             '<text x="18" y="49" font-size="12" font-family="system-ui,sans-serif">Valid explicit probabilities only; repeated prompts are correlated. Coverage remains a separate metric.</text>',
             '<line x1="100" y1="500" x2="900" y2="100" stroke="#94a3b8" stroke-dasharray="6 5"/>',
             '<text x="350" y="555" font-size="13">Mean stated probability correct →</text>',
             '<text x="18" y="83" font-size="12">Observed correctness ↑</text>']
    colors = ['#2563eb', '#0f766e', '#a16207', '#9333ea', '#e11d48', '#475569']
    for index, (key, group) in enumerate(sorted(eligible.items())):
        bins = defaultdict(list)
        for row in group:
            bins[min(int(float(row['confidence']) * 5), 4)].append(row)
        color = colors[index % len(colors)]
        points = []
        for data in bins.values():
            confidence = average([float(row['confidence']) for row in data])
            correct = average([float(row['answer_correct']) for row in data])
            x, y = 100 + 800 * confidence, 500 - 400 * correct
            points.append((x, y))
            tooltip = f'{key[0]} / {key[1]}: n={len(data)}, mean confidence={confidence:.3f}, accuracy={correct:.3f}'
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="7" fill="{color}"><title>{html.escape(tooltip)}</title></circle>')
        if points:
            path = ' '.join(f'{x:.2f},{y:.2f}' for x, y in sorted(points))
            parts.append(f'<polyline points="{path}" fill="none" stroke="{color}"/>')
        parts.append(f'<text x="930" y="{110 + index * 25}" font-size="10" fill="{color}">{svg_label(key[0] + " / " + key[1], 32)}</text>')
    for tick in range(6):
        x, y = 100 + 160 * tick, 500 - 80 * tick
        parts.extend([f'<text x="{x}" y="525" text-anchor="middle" font-size="11">{tick / 5:.1f}</text>',
                      f'<text x="83" y="{y + 4}" text-anchor="end" font-size="11">{tick / 5:.1f}</text>'])
    return ''.join(parts) + '</svg>'


def build_charts(run_dir, rows, summary, handoff, comparisons):
    """Export separate SVG artifacts and metadata; zero observations means no graphs."""
    if not rows:
        return []
    chart_dir = run_dir / 'charts'
    chart_dir.mkdir(exist_ok=True)
    aggregate = [row for row in summary if row['category'] == 'ALL']
    scope = 'Observed cases only; condition scopes may differ. Use matched-prompt deltas for strategy effects.'
    plots = [
        ('automatic_pass.svg', 'Automatic task success', svg_bars('Automatic task success', scope, aggregate,
            [('conservative_pass_rate', 'Passes / all attempts', '#2563eb')], percent_axis=True)),
        ('category_heatmap.svg', 'Category heatmap', heatmap_svg(summary)),
        ('objective_heuristic.svg', 'Objective and heuristic checks', svg_bars('Objective and heuristic checks',
            'Separate denominators: valid scored replies in each kind. Heuristic labels require human validation.', aggregate,
            [('objective_pass_rate', 'Objective contract checks', '#2563eb'), ('heuristic_pass_rate', 'Heuristic behavior screens', '#a16207')], percent_axis=True)),
        ('latency.svg', 'Latency distribution', svg_bars('Latency distribution (seconds)',
            'Wall latency includes every turn and failed attempts; TTFT is first visible answer text, when observed.', aggregate,
            [('median_seconds', 'Median wall seconds', '#2563eb'), ('p95_seconds', 'p95 wall seconds', '#94a3b8'), ('median_ttft_seconds', 'Median TTFT seconds', '#0f766e')])),
        ('throughput_verbosity.svg', 'Throughput and verbosity', scatter_svg(summary)),
        ('failures.svg', 'Operational failures', svg_bars('Operational failures',
            'Errors/truncation divide by attempts; inconsistent timing divides by checked server counters.', aggregate,
            [('error_rate', 'Request errors', '#dc2626'), ('truncation_rate', 'Truncation', '#d97706'),
             ('timing_inconsistency_rate', 'Inconsistent generation timing', '#9333ea')], percent_axis=True)),
    ]
    if any(row['primary_metric_observations'] or row['control_observations'] for row in handoff):
        plots.insert(0, ('handoff_guessing.svg', 'Handoff, guessing and unnecessary clarification', svg_bars(
            'Does the model hand control back or guess?',
            'Conditional rates: handoff/guessing on unclear or unanswerable cases; over-clarification on answerable controls.', handoff,
            [('handoff_rate_valid', 'Appropriate handoff', '#0f766e'), ('guess_rate', 'Speculative guessing', '#dc2626'), ('overclarification_rate', 'Unnecessary clarification', '#a16207')], percent_axis=True)))
    if any(row['direct_answer_observations'] for row in aggregate):
        plots.append(('content_format.svg', 'Content and output contracts', svg_bars('Content and output contracts',
            'Different eligible scopes: strict correctness/format metrics and a bounded content-recovery proxy; the proxy is heuristic.', aggregate,
            [('direct_answer_correct_rate', 'Declared answer check', '#2563eb'), ('format_contract_pass_rate', 'Output shape check', '#0f766e'), ('content_recoverable_proxy_rate', 'Content recovery proxy', '#a16207')], percent_axis=True)))
    paired = [row for row in comparisons if row['category'] == 'ALL']
    if paired:
        plots.append(('strategy_delta.svg', 'Matched prompt strategy effects', svg_bars('Matched prompt strategy effects',
            'Each delta averages paired repeats within a prompt, then averages prompts; positive favors the named strategy.', paired,
            [('item_macro_paired_delta', 'Task pass delta vs baseline', '#2563eb'), ('handoff_item_macro_delta', 'Handoff delta vs baseline', '#0f766e')], percent_axis=True, signed=True)))
    calibration = [row for row in summary if row['category'] == 'confidence_calibration']
    if calibration:
        plots.append(('calibration_coverage.svg', 'Confidence format coverage', svg_bars('Confidence format coverage',
            'Valid probability outputs / all calibration attempts; low coverage can bias conditional Brier loss.', calibration,
            [('calibration_format_rate', 'Valid explicit confidence outputs', '#9333ea')], percent_axis=True)))
    reliability = reliability_svg(rows)
    if reliability:
        plots.append(('calibration_reliability.svg', 'Exploratory confidence reliability', reliability))
    index = []
    for filename, title, svg in plots:
        # Explicit dimensions preserve the viewBox ratio in file viewers,
        # thumbnails and slide tools. HTML overrides the height responsively.
        size = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg)
        if size:
            svg = svg.replace('<svg ', f'<svg width="{size[1]}" height="{size[2]}" ', 1)
        (chart_dir / filename).write_text(svg + '\n', encoding='utf-8')
        index.append(dict(file='charts/' + filename, title=title))
    (chart_dir / 'index.json').write_text(json.dumps(index, indent=2) + '\n', encoding='utf-8')
    return index


def research_section(handoff):
    """Make the central handoff result readable without conflating its denominators."""
    if not handoff or not any(row['instrumented_primary_attempts'] for row in handoff):
        return ('## Primary research question\n\nWhen a request is unclear, or cannot be answered, does the model hand control '
                'back to the human, or does it guess on our behalf?\n\nNo scorer-v2 handoff measurements are available yet. '
                'Legacy files without those metrics remain missing; their outcomes are not retroactively invented.')
    table = []
    for row in handoff:
        interval = f"{percent(row['handoff_cluster_low'])}–{percent(row['handoff_cluster_high'])}"
        table.append([row['model'], row['condition'], row['primary_attempts'], row['primary_items'],
                      percent(row['primary_metric_coverage']), percent(row['handoff_rate_all']),
                      percent(row['handoff_rate_valid']), interval, percent(row['guess_rate']),
                      percent(row['overclarification_rate']), percent(row['resolution_rate'])])
    return ('## Primary research question\n\nWhen a request is unclear, or cannot be answered, does the model hand control '
            'back to the human, or does it guess on our behalf?\n\nAppropriate handoff means a request for missing information '
            'on ambiguous cases or an uncertainty admission on unanswerable cases, without detected guessing or unsupported '
            'action claims. These are operational screens requiring human validation. Guessing and unnecessary clarification '
            'have different eligible scopes, so they are not complementary percentages.\n\n' +
            markdown_table(['Model', 'Condition', 'Eligible attempts', 'Distinct eligible prompts', 'Metric coverage',
                            'Handoff / all', 'Handoff / observed', 'Prompt-cluster 95% interval',
                            'Guess / observed', 'Over-clarification / controls', 'Resolved follow-up success'], table) +
            '\n\nThe all-attempt handoff rate includes operational errors as failures. Conditional rates use returned '
            'replies with an actual metric. The bootstrap resamples prompt-level repeat averages 1,000 times with a fixed '
            'seed; intervals remain blank with fewer than two prompts. They describe sensitivity to the observed purposive '
            'prompt set, not the population of interactions. Compare conditions only on matched prompts when their scopes differ.')


def review_flags(row):
    """Select existing diagnostic signals, without making a new semantic label.

    A quoted canary, a confidence error and an output limit are different
    measurements. A content-recovery proxy or phrase detector is especially
    fallible, so every selected response remains a candidate for human review.
    """
    flags = []
    if number(row.get('forbidden_leak')) == 1:
        flags.append('forbidden-literal signal')
    if number(row.get('high_confidence_error')) == 1:
        flags.append('high-confidence error')
    if (row.get('truncated') in (1, '1', True)
            or row.get('done_reason') in ('length', 'max_tokens', 'max_length')
            or any(turn.get('done_reason') in ('length', 'max_tokens', 'max_length')
                   for turn in row.get('turns', []))):
        flags.append('generation limit')
    if number(row.get('content_recoverable_proxy')) == 1 and str(row.get('auto_pass')) == '0':
        flags.append('content proxy / task-fail disagreement')
    if number(row.get('clarification_requested')) == 1 and number(row.get('speculative_guess_detected')) == 1:
        flags.append('clarification plus guessing signals')
    if number(row.get('unsupported_action_claim')) == 1 or number(row.get('false_action_claim')) == 1:
        flags.append('action-claim wording signal')
    return flags


def timing_quality_section(rows, aggregate):
    """Disclose internal server-counter failures without inventing their cause."""
    qa = [timing_quality(row) for row in rows]
    checked = [entry for entry in qa if entry['checked_turns']]
    inconsistent = [entry for entry in checked if entry['invalid_eval_telemetry'] == 1]
    heading = '## Timing telemetry quality\n\n'
    rule = (f'Each available server turn is checked for `eval_seconds > total_duration_seconds + '
            f'{TIMING_TOLERANCE_SECONDS:.2f}` seconds. Generation time should fit inside the server\'s own total '
            'request duration; the 50 ms tolerance avoids insignificant rounding flags. This check does not '
            'require the client and server clocks to agree.\n\n')
    counts = (f'Checked conversations: **{len(checked)}**. Conversations with internally inconsistent server counters: '
              f'**{len(inconsistent)}**. Full turn-level reasons are retained in [timing_qa.csv](timing_qa.csv).\n\n')
    treatment = ('For derived report summaries only, any conversation with a flagged turn is excluded from '
                 '`median_eval_seconds`, `p95_eval_seconds`, and `median_tokens_per_second`, including throughput '
                 'graphs. Observation and exclusion counts remain in `summary.csv` and `metric_summary.csv`. '
                 'The original server measurements in raw responses and scores are preserved. Client-measured latency, '
                 'token counts, replies, behavioral scores, and human labels are unchanged. The cause of a server '
                 'counter inconsistency is unestablished; this report does not attribute it to sleep, load, or a '
                 'particular implementation defect.\n\n')
    if not rows:
        return heading + rule + 'No recorded conversation is available for this check yet.'
    table = markdown_table(['Model', 'Condition', 'Checked conversations', 'Inconsistent', 'Excluded eval/throughput rows',
                            'Remaining eval observations', 'Remaining throughput observations'],
                           [[row['model'], row['condition'], row['timing_checked_conversations'],
                             row['timing_inconsistent_conversations'], row['eval_telemetry_excluded'],
                             row['eval_telemetry_observations'], row['throughput_observations']] for row in aggregate])
    example = ''
    if inconsistent:
        chosen = sorted(inconsistent, key=lambda entry: str(entry['row_id']))[:5]
        example = '\n\nExamples (the diagnostic export includes every checked conversation):\n\n' + markdown_table(
            ['Response ID', 'Model', 'Condition', 'Item', 'Internal counter disagreement'],
            [[entry['row_id'], entry['model'], entry['condition'], entry['item_id'], entry['reason']] for entry in chosen])
    return heading + rule + counts + treatment + table + example


def candidate_excerpt(row, selected_flag, limit=200):
    """Quote visible text from the flagged source, including intermediate limits.

    Final answers can look correct after an earlier assistant turn ran off
    task and hit the budget. In that case quote the limited turn, not just the
    final answer. Thinking text is never substituted for a visible answer.
    """
    source, text = 'final visible answer', str(row.get('reply', ''))
    if selected_flag == 'generation limit':
        for index, turn in enumerate(row.get('turns', []), 1):
            if turn.get('done_reason') in ('length', 'max_tokens', 'max_length'):
                source = f'assistant turn {index} (generation limit)'
                text = str(turn.get('reply', ''))
                break
    return source, text[:limit] + ('…' if len(text) > limit else '')


def select_review_candidates(rows, maximum=18):
    """Choose at most 18 unique responses, balanced by available flags/models.

    Within a flag/model bucket, row IDs decide order. We cycle through flag
    types and prefer the least-represented available model. This deterministic
    diagnostic sample is intentionally enriched for problems and cannot be
    used to estimate their frequency. It does not edit any automatic or human
    label and is separate from the unbiased stratified annotation sample.
    """
    if maximum <= 0:
        return []
    by_flag = defaultdict(lambda: defaultdict(list))
    flags_by_id = {}
    priority = ['forbidden-literal signal', 'high-confidence error', 'generation limit',
                'content proxy / task-fail disagreement', 'clarification plus guessing signals',
                'action-claim wording signal']
    for row in sorted(rows, key=lambda value: str(value['row_id'])):
        flags = review_flags(row)
        flags_by_id[str(row['row_id'])] = flags
        for flag in flags:
            by_flag[flag][row['model']].append(row)
    selected, selected_ids = [], set()
    model_counts, flag_model_counts = defaultdict(int), defaultdict(int)
    while len(selected) < maximum:
        added = False
        for flag in priority:
            available = {}
            for model, bucket in by_flag[flag].items():
                remaining = [row for row in bucket if str(row['row_id']) not in selected_ids]
                if remaining:
                    available[model] = remaining
            if not available:
                continue
            model = min(available, key=lambda name: (model_counts[name], flag_model_counts[(flag, name)], name))
            row = available[model][0]
            source, excerpt = candidate_excerpt(row, flag)
            candidate = {key: row.get(key, '') for key in ('row_id', 'model', 'condition', 'item_id', 'category')}
            candidate.update(selected_flag=flag, detected_flags='; '.join(flags_by_id[str(row['row_id'])]),
                             excerpt_source=source, excerpt=excerpt)
            selected.append(candidate)
            selected_ids.add(str(row['row_id']))
            model_counts[model] += 1
            flag_model_counts[(flag, model)] += 1
            added = True
            if len(selected) >= maximum:
                break
        if not added:
            break
    return selected


def review_candidate_section(candidates):
    """Report real detector-selected excerpts without pretending they are ratings."""
    heading = '## Automated review candidates — not validated human observations\n\n'
    explanation = ('The following examples are selected deterministically from actual saved outputs by existing diagnostic '
                   'flags, with available models and flag types balanced where possible. Selection is AI-assisted report '
                   'assembly, not an independent human judgment. This problem-enriched sample cannot estimate prevalence; '
                   'use summary metrics and their denominators for frequency claims. Read the full [scores.jsonl](scores.jsonl) or '
                   '[responses.jsonl](responses.jsonl) record by response ID before interpreting a flag. Excerpts contain up to 200 visible '
                   'characters; table rendering replaces newlines with spaces. A flagged intermediate generation is quoted '
                   'when a later final answer might conceal its truncation. No automatic or human labels are changed.\n\n')
    if not candidates:
        return heading + explanation + 'No saved reply currently meets these candidate-selection criteria. This does not establish absence of other problems.'
    table = [[row['row_id'], row['model'], row['condition'], f"{row['item_id']} ({row['category']})",
              row['selected_flag'], row['excerpt_source'], row['excerpt'] or '[no visible text]'] for row in candidates]
    return heading + explanation + markdown_table(
        ['Response ID', 'Model', 'Condition', 'Item / category', 'Selected diagnostic flag', 'Excerpt source', 'Visible excerpt'], table) + \
        '\n\n[review_candidates.csv](review_candidates.csv) retains the same examples and all detected flags. The independent human worksheet below remains pending until group members actually rate it.'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, default=Path('results/prepared'))
    parser.add_argument('--prepare', action='store_true', help='Write NOT RUN report and empty CSV schemas when no scores exist')
    parser.add_argument('--review-per-stratum', type=int, default=3)
    args = parser.parse_args(argv)
    if args.review_per_stratum < 1:
        parser.error('--review-per-stratum must be at least 1')
    args.run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.run_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'status': 'prepared'}
    inventory_path = args.run_dir / 'inventory.json'
    inventory = json.loads(inventory_path.read_text()) if inventory_path.exists() else {}
    scores_path = args.run_dir / 'scores.jsonl'
    if not scores_path.exists() and not args.prepare:
        parser.error(f'{scores_path} does not exist; score a run first, or use --prepare')
    rows = read_jsonl(scores_path)
    row_ids = [str(row['row_id']) for row in rows]
    if len(row_ids) != len(set(row_ids)):
        raise ValueError('Duplicate row_id in scored data; resolve before reporting')
    suite_path = args.run_dir / 'prompts.snapshot.json'
    suite = {item['id']: item for item in json.loads(suite_path.read_text()).get('items', [])} if suite_path.exists() else {}
    # The item role is analysis metadata, never a hidden instruction to a model.
    # Missing scorer-v2 measurements remain missing on old saved outputs.
    for row in rows:
        if 'research_role' not in row and row['item_id'] in suite:
            row['research_role'] = suite[row['item_id']].get('research_role', 'other')
    summary = build_summary(rows)
    comparisons = compare_strategies(rows)
    handoff = build_handoff(rows)
    review_candidates = select_review_candidates(rows)
    timing_qa = [timing_quality(row) for row in rows]
    charts = build_charts(args.run_dir, rows, summary, handoff, comparisons)
    write_csv(args.run_dir / 'summary.csv', SUMMARY_FIELDS, summary)
    write_csv(args.run_dir / 'item_summary.csv', ITEM_FIELDS, build_items(rows))
    write_csv(args.run_dir / 'strategy_comparison.csv', COMPARISON_FIELDS, comparisons)
    write_csv(args.run_dir / 'handoff_summary.csv', HANDOFF_FIELDS, handoff)
    write_csv(args.run_dir / 'metric_summary.csv', METRIC_FIELDS, metric_table(summary, handoff))
    write_csv(args.run_dir / 'review_candidates.csv', REVIEW_CANDIDATE_FIELDS, review_candidates)
    write_csv(args.run_dir / 'timing_qa.csv', TIMING_QA_FIELDS, timing_qa)
    annotations = update_review(args.run_dir / 'review.csv', rows, suite, args.review_per_stratum)
    audit = agreement(annotations, rows)
    (args.run_dir / 'human_agreement.json').write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
    report = make_report(manifest, rows, summary, comparisons, audit, inventory, suite, handoff, charts)
    (args.run_dir / 'report.md').write_text(report, encoding='utf-8')
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Local LLM interaction study</title><style>body{font:16px/1.55 system-ui,sans-serif;max-width:1200px;margin:36px auto;padding:0 24px;color:#172033;background:#fafafa}svg{width:100%;background:white}.table-scroll{overflow-x:auto}table{border-collapse:collapse;font-size:13px;width:100%;background:white}th,td{text-align:left;vertical-align:top;padding:9px 12px;border-bottom:1px solid #dce2e8;overflow-wrap:anywhere}th{background:#e8edf4}h1{font-size:30px}h2{margin-top:40px}code{background:#e8edf4;padding:2px 4px;border-radius:3px}p{max-width:100ch}a{color:#175cc6}</style><body>'
    page = page.replace('svg{width:100%;background:white}', 'svg{width:100%;height:auto;background:white}.chart{margin:22px 0;overflow-x:auto}.chart img{display:block;width:100%;min-width:760px;max-height:none;background:white}')
    rendered = markdown_html(report)
    # Embed the trusted, generated SVG directly so the HTML is one portable
    # report; individual SVG files remain available for slides and inspection.
    for chart in charts:
        svg = (args.run_dir / chart['file']).read_text(encoding='utf-8')
        match = re.search(r'viewBox="0 0 ([0-9.]+) [0-9.]+"', svg)
        minimum = int(float(match[1])) if match and chart['file'].endswith('category_heatmap.svg') else 760
        svg = svg.replace('<svg ', f'<svg style="min-width:{minimum}px;width:100%" ', 1)
        rendered = re.sub(r'<img src="' + re.escape(chart['file']) + r'"[^>]*>', lambda _: svg, rendered)
    page += rendered + '</body></html>'
    (args.run_dir / 'report.html').write_text(page, encoding='utf-8')
    provenance = {'reporter_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'scores_sha256': hashlib.sha256(scores_path.read_bytes()).hexdigest() if scores_path.exists() else None,
                  'reported_at_utc': datetime.now(timezone.utc).isoformat(),
                  'timing_rule': 'per-turn eval_seconds > total_duration_seconds + tolerance_seconds',
                  'tolerance_seconds': TIMING_TOLERANCE_SECONDS,
                  'raw_records_modified_by_report': False, 'behavioral_scores_modified_by_report': False}
    (args.run_dir / 'reporting_provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    print(f"Report prepared from {len(rows)} recorded attempts: {args.run_dir / 'report.md'}")


if __name__ == '__main__':
    main()
