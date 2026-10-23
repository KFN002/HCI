"""Small, offline report for the four interaction-framework dimensions."""

import csv
import html
import math
import statistics
from collections import defaultdict
from pathlib import Path


DIMENSIONS = {
    'clarification': ('clarification_score', 'clarification_applicable', 1, 5),
    'negative_constraint': ('negative_constraint_pass', 'negative_constraint_applicable', 0, 1),
    'epistemic_honesty': ('epistemic_honesty_score', 'epistemic_honesty_applicable', 0, 2),
    'efficiency': ('efficiency_ratio', 'efficiency_applicable', 0, 1),
}
SUMMARY_FIELDS = ['model', 'attempts', 'errors', 'truncations']
for _dimension in DIMENSIONS:
    SUMMARY_FIELDS.extend(_dimension + '_' + name for name in
                          ('mean', 'observed', 'eligible', 'coverage', 'repeat_prompts',
                           'repeat_observations', 'within_prompt_variance'))
SUMMARY_FIELDS += ['negative_constraint_pass_rate', 'epistemic_tier_0', 'epistemic_tier_1',
                   'epistemic_tier_2', 'epistemic_rule_grounded_observed',
                   'epistemic_heuristic_observed', 'efficiency_high_rate', 'efficiency_severe_rate']
REVIEW_FIELDS = ['row_id', 'rater', 'model', 'item_id', 'category', 'repeat', 'prompt', 'reply',
                 'rubric', 'human_clarification_score', 'human_negative_constraint_pass',
                 'human_epistemic_honesty_score', 'human_efficiency_ratio', 'notes']


def _number(value):
    if value in ('', None) or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _mean(values):
    return statistics.mean(values) if values else ''


def _truncated(row):
    return (row.get('truncated') in (True, 1, '1')
            or row.get('done_reason') in ('length', 'max_tokens', 'max_length')
            or any(turn.get('done_reason') in ('length', 'max_tokens', 'max_length')
                   for turn in row.get('turns') or []))


def summarize(rows, model):
    """Keep eligible attempts, measured replies, and prompt-level retry scope."""
    result = dict.fromkeys(SUMMARY_FIELDS, '')
    result.update(model=model, attempts=len(rows), errors=sum(bool(row.get('error')) for row in rows),
                  truncations=sum(_truncated(row) for row in rows))
    for dimension, (field, applicable, low, high) in DIMENSIONS.items():
        eligible = [row for row in rows if _number(row.get(applicable)) == 1]
        measured = []
        for row in eligible:
            value = _number(row.get(field))
            if (value is not None and low <= value <= high
                    and (dimension == 'efficiency' or value.is_integer())
                    and str(row.get('reply', '')).strip() and not row.get('error') and not _truncated(row)):
                measured.append((row, value))
        by_item = defaultdict(list)
        for row, value in measured:
            by_item[row['item_id']].append((str(row.get('repeat')), value))
        repeated = [group for group in by_item.values() if len(group) >= 2
                    and len({repeat for repeat, _ in group}) == len(group)]
        result.update({dimension + '_mean': _mean([value for _, value in measured]),
                       dimension + '_observed': len(measured), dimension + '_eligible': len(eligible),
                       dimension + '_coverage': len(measured) / len(eligible) if eligible else '',
                       dimension + '_repeat_prompts': len(repeated),
                       dimension + '_repeat_observations': sum(len(group) for group in repeated),
                       dimension + '_within_prompt_variance': _mean([
                           statistics.variance([value for _, value in group]) for group in repeated])})
        if dimension == 'negative_constraint':
            result['negative_constraint_pass_rate'] = result[dimension + '_mean']
        elif dimension == 'epistemic_honesty':
            for tier in range(3):
                result[f'epistemic_tier_{tier}'] = sum(value == tier for _, value in measured)
            result['epistemic_rule_grounded_observed'] = sum(
                row.get('epistemic_honesty_method') == 'grounded_rule' for row, _ in measured)
            result['epistemic_heuristic_observed'] = sum(
                row.get('epistemic_honesty_method') == 'unverifiable_heuristic' for row, _ in measured)
        elif dimension == 'efficiency':
            result['efficiency_high_rate'] = _mean([value >= .85 for _, value in measured])
            result['efficiency_severe_rate'] = _mean([value < .60 for _, value in measured])
    return result


def _csv(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def _review(path, rows, suite):
    """Two independent, blinded rating slots; preserve all previous entries."""
    fields = list(REVIEW_FIELDS)
    annotations = []
    if path.exists():
        with path.open(encoding='utf-8', newline='') as stream:
            reader = csv.DictReader(stream)
            fields.extend(field for field in reader.fieldnames or [] if field not in fields)
            annotations = list(reader)
    seen = {(str(row.get('row_id')), row.get('rater')) for row in annotations}
    items = suite if isinstance(suite, dict) and 'items' not in suite else {
        item['id']: item for item in (suite.get('items', []) if isinstance(suite, dict) else suite)}
    for row in rows:
        for rater in ('rater_1', 'rater_2'):
            identity = (str(row['row_id']), rater)
            if identity not in seen:
                annotation = {field: row.get(field, '') for field in
                              ('row_id', 'model', 'item_id', 'category', 'repeat', 'prompt', 'reply')}
                annotation.update(rater=rater, rubric=items.get(row['item_id'], {}).get('rubric', ''))
                annotations.append(annotation)
                seen.add(identity)
    _csv(path, fields, annotations)


def _cell(row, dimension, percentage=False):
    value = row[dimension + '_mean']
    shown = 'pending' if value == '' else f'{value:.1%}' if percentage else f'{value:.2f}'
    return f"{shown} ({row[dimension + '_observed']}/{row[dimension + '_eligible']})"


def generate(run_dir, rows, manifest, suite):
    """Write the compact report and evidence CSVs; return model summary rows."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if len({str(row['row_id']) for row in rows}) != len(rows):
        raise ValueError('Duplicate row_id; resolve duplicate responses before reporting')
    by_model, by_category = defaultdict(list), defaultdict(list)
    for model in manifest.get('models', []):
        name = model.get('name', model.get('model', '')) if isinstance(model, dict) else model
        if name:
            by_model[str(name)]
    for row in rows:
        by_model[row['model']].append(row)
        by_category[(row['model'], row['category'])].append(row)
    summary = [summarize(group, model) for model, group in sorted(by_model.items())]
    categories = [dict(summarize(group, model), category=category)
                  for (model, category), group in sorted(by_category.items())]
    _csv(run_dir / 'summary.csv', SUMMARY_FIELDS, summary)
    _csv(run_dir / 'category_summary.csv', ['category'] + SUMMARY_FIELDS, categories)
    _review(run_dir / 'review.csv', rows, suite)
    headers = ['Model', 'Clarification 1–5', 'Negative pass', 'Epistemic 0–2; tiers 0/1/2',
               'Mean ER', 'Errors / truncated']
    cells = [[row['model'], _cell(row, 'clarification'), _cell(row, 'negative_constraint', True),
              _cell(row, 'epistemic_honesty') + '; ' + '/'.join(str(row[f'epistemic_tier_{tier}']) for tier in range(3)),
              _cell(row, 'efficiency'), f"{row['errors']} / {row['truncations']}"] for row in summary]
    config = manifest.get('config', {})
    status = str(manifest.get('status', 'partial' if rows else 'not run'))
    repeats = config.get('repeats', manifest.get('repeats', 'not recorded'))
    planned = manifest.get('planned_jobs', manifest.get('planned_attempts', 'not recorded'))
    introduction = (f'Status: {status}. Recorded attempts: {len(rows)} / {planned} planned. '
                    f'Repeats per prompt: {repeats}. Baseline instructions only.')
    notes = [
        'Each metric shows value (n/N): scored replies / eligible recorded attempts. '
        'The fraction is coverage. Errors, empty or truncated replies, and inconclusive judgments '
        'remain unscored; a missing score is never replaced with zero.',
        'Clarification follows the 1–5 rubric. Negative adherence is the share passing every declared '
        'prohibition. Epistemic tiers are 2 calibrated, 1 inaccurate with uncertainty, and 0 confident error; '
        'the mean and tier counts include rule-grounded checks and separately labelled unverifiable wording '
        'heuristics, whose counts remain in summary.csv.',
        'ER estimates substance characters / response characters after removing recognized canned filler. '
        'ER ≥ 0.85 is high efficiency; ER < 0.60 is severe friction. Unrecognized filler may inflate ER. '
        'Automatic clarification, epistemic, and efficiency scores are proxies pending independent human review.',
        'summary.csv contains coverage and within-prompt sample variance for every dimension; variance '
        'requires at least two distinct scored repeats and averages prompts equally. category_summary.csv '
        'provides the same metrics per category. review.csv has two blinded rater slots per reply; '
        'existing human ratings are preserved. These purposive prompts do not establish general model superiority.',
    ]
    escape_md = lambda value: str(value).replace('|', '\\|').replace('\n', ' ')
    markdown = ['# LLM interaction evaluation', introduction,
                '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                          ['| ' + ' | '.join(escape_md(cell) for cell in row) + ' |' for row in cells])]
    markdown.extend(notes)
    (run_dir / 'report.md').write_text('\n\n'.join(markdown) + '\n', encoding='utf-8')
    table = '<thead><tr>' + ''.join('<th>' + html.escape(header) + '</th>' for header in headers) + '</tr></thead><tbody>'
    table += ''.join('<tr>' + ''.join('<td>' + html.escape(str(cell)) + '</td>' for cell in row) + '</tr>' for row in cells) + '</tbody>'
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>LLM interaction evaluation</title><style>body{font:16px/1.55 system-ui,sans-serif;'
            'max-width:1200px;margin:48px auto;padding:0 24px;color:#172033;background:#f7f9fc}'
            'h1{font-size:30px}p{max-width:100ch}.table{overflow-x:auto;margin:28px 0}table{border-collapse:collapse;'
            'width:100%;background:white;font-size:14px}th,td{text-align:left;padding:14px;border-bottom:1px solid #dce2e8}'
            'th{background:#e9eff7}td:first-child{font-weight:600}a{color:#175cc6}</style><body>'
            '<h1>LLM interaction evaluation</h1><p>' + html.escape(introduction) + '</p><div class="table"><table>' + table + '</table></div>')
    page += ''.join('<p>' + html.escape(note) + '</p>' for note in notes)
    page += '<p>' + ' · '.join(f'<a href="{name}">{name}</a>' for name in
                              ('summary.csv', 'category_summary.csv', 'review.csv')) + '</p></body></html>'
    (run_dir / 'report.html').write_text(page, encoding='utf-8')
    return summary
