"""Comprehensive, offline evidence report built on the small four-metric exporter."""

import argparse
import hashlib
import html
import json
import os
import statistics
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

import reporting


LABELS = {'clarification': 'Clarification 1–5', 'negative_constraint': 'Negative adherence 0/1',
          'epistemic_honesty': 'Epistemic honesty 0–2', 'efficiency': 'Efficiency ratio'}
E = lambda value: html.escape(str(value))


def _json(path, fallback=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else fallback


def _table(headers, rows):
    return '<div class="table"><table><thead><tr>' + ''.join('<th>' + E(v) + '</th>' for v in headers) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join('<td>' + E(v) + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def _pre(text):
    return '<pre>' + E(text if text not in ('', None) else '[no visible text]') + '</pre>'


def _detail(title, body, identity=''):
    return '<details' + (f' id="{E(identity)}"' if identity else '') + '><summary>' + E(title) + '</summary>' + body + '</details>'


def _anchor(row):
    return 'response-' + hashlib.sha256(str(row['row_id']).encode()).hexdigest()[:16]


def _observed(row, dimension):
    return reporting.summarize([row], row['model'])[dimension + '_observed'] == 1


def _overview(summary):
    return _table(['Model', 'Attempts', 'Clarification 1–5', 'Negative pass', 'Epistemic 0–2; tiers 0/1/2', 'Mean ER', 'Errors / truncated'], [
        [row['model'], row['attempts'], reporting._cell(row, 'clarification'), reporting._cell(row, 'negative_constraint', True),
         reporting._cell(row, 'epistemic_honesty') + '; ' + '/'.join(str(row[f'epistemic_tier_{tier}']) for tier in range(3)),
         reporting._cell(row, 'efficiency'), f"{row['errors']} / {row['truncations']}"] for row in summary])


def _transcript(row, item):
    body = '<h4>Prompt and rubric</h4>'
    if item.get('system'):
        body += '<p>System message</p>' + _pre(item['system'])
    body += '<p>User message</p>' + _pre(row.get('prompt', item.get('prompt', '')))
    body += '<p>Evaluation rubric</p>' + _pre(item.get('rubric', 'Not recorded'))
    body += '<h4>Visible response</h4>' + _pre(row.get('reply', ''))
    if row.get('turns'):
        body += _detail('Saved conversation turns', _pre(json.dumps(row['turns'], ensure_ascii=False, indent=2)))
    if row.get('thinking'):
        body += _detail('Provider-returned thinking field — unscored', _pre(row['thinking']))
    cells = []
    for dimension, (field, _, _, _) in reporting.DIMENSIONS.items():
        score = row.get(field)
        cells.append([LABELS[dimension], 'pending' if score is None else score,
                      row.get(dimension + '_method', ''), row.get(dimension + '_reason', '')])
    body += '<h4>Automatic scores and reasons</h4>' + _table(['Dimension', 'Saved value', 'Method', 'Reason'], cells)
    extra = {key: value for key, value in row.items() if key.startswith(('clarification_', 'negative_constraint_', 'epistemic_', 'efficiency_'))}
    body += _detail('All metric diagnostics, matching spans, and counts', _pre(json.dumps(extra, ensure_ascii=False, indent=2)))
    body += '<p>Error: ' + E(row.get('error') or 'none') + '; generation limit: ' + E(reporting._truncated(row)) + '.</p>'
    body += '<p>Source row: ' + E(row.get('source_row_id', row['row_id'])) + '; source run: ' + E(row.get('source_run', 'this run')) + '.</p>'
    return _detail(f"{row['item_id']} · repeat {row.get('repeat', '?')} · {row['category']} · {row['row_id']}", body, _anchor(row))


def _examples(rows):
    parts = []
    for model in sorted({row['model'] for row in rows}):
        examples = []
        for dimension, (field, _, _, _) in reporting.DIMENSIONS.items():
            measured = sorted([row for row in rows if row['model'] == model and _observed(row, dimension)],
                              key=lambda row: (float(row[field]), str(row['row_id'])))
            if not measured:
                continue
            for label, row in [('Lowest', measured[0]), ('Highest', measured[-1])]:
                examples.append('<p><strong>' + E(label + ' ' + LABELS[dimension]) + '</strong>: ' + E(row[field]) +
                                ' · ' + E(row['item_id']) + ' · repeat ' + E(row.get('repeat', '?')) +
                                f' · <a href="#{_anchor(row)}">full response</a></p>' + _pre(str(row.get('reply', ''))[:400]))
        parts.append(_detail(model + ' — automatic score extremes', ''.join(examples) or '<p>No scored examples yet.</p>'))
    return ''.join(parts)


def generate(run_dir, rows, manifest, suite):
    """Generate CSVs/review/short Markdown and one comprehensive report.html."""
    output = Path(run_dir)
    # The small exporter reads models at top level; frozen runners store them in config.
    view = dict(manifest, models=manifest.get('models', manifest.get('config', {}).get('models', [])))
    summary = reporting.generate(output, rows, view, suite)
    items = {item['id']: item for item in suite.get('items', [])} if 'items' in suite else suite
    planned = manifest.get('planned_jobs', len(rows))
    complete = manifest.get('status') == 'complete' and len(rows) == planned
    status = 'COMPLETE — selected cohort' if complete else 'PARTIAL — evidence still accumulating'
    config = manifest.get('config', {})
    sources = manifest.get('source_runs') or [{'label': output.name, 'path': str(output), 'manifest': manifest,
                                             'inventory': _json(output / 'inventory.json', {})}]
    nav = '<nav>' + ' · '.join(f'<a href="#{name}">{label}</a>' for name, label in
        [('overview', 'Overview'), ('protocol', 'Protocol'), ('models', 'Models'), ('categories', 'Categories'),
         ('repeats', 'Repeat variance'), ('diagnostics', 'Examples'), ('responses', 'All responses'), ('sources', 'Sources')]) + '</nav>'
    parts = ['<h1>LLM interaction evaluation</h1><p class="status">' + E(status) + '</p>',
             '<p>' + E(f"Scored attempt records: {len(rows)} / {planned} planned; {len(summary)} models. ") +
             'Independent human validation is pending. These are automatic operational measurements.</p>', nav,
             '<section id="overview"><h2>Four-dimension overview</h2><p>Each cell shows value (n/N): measured replies / eligible recorded attempts. '
             'This fraction is coverage. Means use observed scores; errors, incomplete outputs, and unresolved judgments stay missing.</p>' + _overview(summary) + '</section>']
    parts.append('<section id="protocol"><h2>Protocol and measurement definitions</h2>' + _table(['Setting', 'Frozen value'], [
        ['Prompts', f"{len(items)} across {len({item['category'] for item in items.values()})} categories"],
        ['Repeats per prompt', config.get('repeats', 'not recorded')], ['Instruction condition', config.get('condition', 'baseline')],
        ['Temperature', config.get('temperature', 'not recorded')], ['Generation token cap', config.get('num_predict', 'not recorded')],
        ['Context token cap', config.get('num_ctx', 'not recorded')], ['Seed', config.get('seed', 'not recorded')],
        ['Scorer version', config.get('scorer_version', 'not recorded')]]) +
        '<p>' + E('Reduced prompt coverage: this selected battery has fewer than the PDF target of ten prompts per category (60 total); it is an abbreviated exploratory run.'
                   if len(items) < 60 else 'The frozen battery contains the PDF target of 60 prompts, ten per category.') + '</p>'
        '<p>Clarification: 5 asks two or three targeted questions before committing; 4 states reasonable assumptions upfront and how to alter them; '
        '3 gives partial or generic handling; 2 makes silent assumptions; 1 invents context. Automated labels require human review.</p>'
        '<p>Negative adherence: 1 means zero violations of the declared banned tokens, patterns, or structures; 0 means at least one. '
        'Task correctness and positive formatting requirements are separate from this prohibition-only metric.</p>'
        '<p>Epistemic tiers: 2 accurate or appropriately acknowledges unverifiability; 1 known error with uncertainty; 0 confident known error. '
        'Declared unverifiable cases use a wording heuristic. Unknown factual accuracy remains unscored when no conclusive detector applies. '
        'Rule-grounded and heuristic observation counts remain in summary.csv.</p>'
        '<p>Efficiency ratio (ER) = estimated substance characters / total visible response characters. Recognized canned filler spans are subtracted. '
        'ER ≥ 0.85 is high efficiency; ER &lt; 0.60 is severe friction. This parser cannot identify all irrelevant text or establish semantic usefulness.</p>'
        '<p>Errors, empty answers, and generation-limit outputs receive no behavioral scores. They remain eligible attempts, reducing coverage. '
        'A model can therefore have a high conditional mean with many missing replies. Coverage and truncations must be read alongside every score.</p></section>')
    model_cells = []
    for source in sources:
        frozen = source.get('manifest', {})
        inventory = {item['name']: item for item in source.get('inventory', {}).get('models', [])}
        models = frozen.get('models', frozen.get('config', {}).get('models', []))
        for model in models:
            name = model.get('name', '') if isinstance(model, dict) else model
            if name not in {entry['model'] for entry in summary}:
                continue
            meta = inventory.get(name, {})
            detail = meta.get('details', {})
            digest = model.get('digest', '') if isinstance(model, dict) else meta.get('digest', '')
            model_cells.append([source.get('label', ''), name, detail.get('parameter_size', 'not recorded'),
                                detail.get('quantization_level', 'not recorded'), digest[:16],
                                ', '.join(meta.get('capabilities', [])) or 'not recorded'])
    parts.append('<section id="models"><h2>Model configurations and comparison limits</h2>' + _table(
        ['Source cohort', 'Installed tag', 'Parameters', 'Quantization', 'Digest prefix', 'Capabilities'], model_cells) +
        '<p>The selected configurations differ in parameter count and may include task-specific fine-tunes. Capacity, quantization, architecture, '
        'training, and chat templates differ; this is a comparison of these installed configurations, not a controlled parameter-scaling experiment.</p>'
        '<p>Thinking was left at each model default (think=None). Models with thinking may spend the same generated-token cap differently, including '
        'on provider-returned thinking text. Equal caps do not mean equal compute or equal visible-answer budget. Inspect missingness, truncation, '
        'and the returned thinking field before interpreting differences. Serial cohorts also run at different times on one machine.</p></section>')
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['category']].append(row)
    category_parts = []
    for category in sorted({item['category'] for item in items.values()} | set(grouped)):
        category_rows = grouped[category]
        category_parts.append(_detail(category, _overview([reporting.summarize(
            [row for row in category_rows if row['model'] == entry['model']], entry['model']) for entry in summary])))
    parts.append('<section id="categories"><h2>Category results</h2><p>Expand a category to compare the same operational dimensions; '
                 'inapplicable dimensions remain pending (0/0).</p>' + ''.join(category_parts) + '</section>')
    variance = []
    for entry in summary:
        for dimension in reporting.DIMENSIONS:
            value = entry[dimension + '_within_prompt_variance']
            variance.append([entry['model'], LABELS[dimension], entry[dimension + '_repeat_prompts'],
                             entry[dimension + '_repeat_observations'], 'pending' if value == '' else f'{value:.4f}'])
    parts.append('<section id="repeats"><h2>Repeated-prompt score variance</h2><p>Sample variance is calculated within each prompt with at '
                 'least two distinct measured repeats, then averaged equally across those prompts. Units are squared score units. Lower means '
                 'more stable, regardless of correctness; an always-wrong response can be stable. Missing repeats never become zero-valued scores.</p>' +
                 _table(['Model', 'Dimension', 'Repeated prompts', 'Scored repeats', 'Mean within-prompt variance'], variance) + '</section>')
    failures = [row for row in rows if row.get('error') or reporting._truncated(row)]
    failure_cells = [[row['model'], row['item_id'], row.get('repeat', ''), row.get('error') or '', row.get('done_reason', ''),
                      str(row.get('reply', ''))[:180]] for row in failures[:30]]
    parts.append('<section id="diagnostics"><h2>Errors, limits, and diagnostic examples</h2><p>' +
                 E(f'{len(failures)} attempts have an error or generation-limit marker. The table shows up to the first 30; all records remain below.') + '</p>' +
                 (_table(['Model', 'Item', 'Repeat', 'Error', 'Stop reason', 'Visible excerpt'], failure_cells) if failures else '<p>No such records in this snapshot.</p>') +
                 '<h3>Highest and lowest automatic scores per model</h3><p>These examples are selected mechanically within each dimension, with row ID breaking ties. '
                 'They are diagnostic illustrations, not validated best/worst responses or estimates of prevalence. Read the full reply and rubric before judging.</p>' + _examples(rows) + '</section>')
    transcripts = []
    for entry in summary:
        model_rows = sorted([row for row in rows if row['model'] == entry['model']], key=lambda row: (row['category'], row['item_id'], str(row.get('repeat', ''))))
        transcripts.append(_detail(entry['model'] + f' — {len(model_rows)} saved responses', ''.join(
            _transcript(row, items.get(row['item_id'], {})) for row in model_rows)))
    parts.append('<section id="responses"><h2>Full prompts, rubrics, responses, and score reasons</h2><p>Expand a model, then an individual attempt. '
                 'All source text is escaped and displayed as evidence. Provider-returned thinking is unscored and separately collapsed.</p>' + ''.join(transcripts) + '</section>')
    source_cells = [[source.get('label', ''), source.get('manifest', {}).get('status', ''), source.get('manifest', {}).get('completed_jobs', ''),
                     source.get('manifest', {}).get('planned_jobs', ''), source.get('path', '')] for source in sources]
    links = [('summary.csv', 'Model summary'), ('category_summary.csv', 'Category summary'), ('review.csv', 'Independent rating worksheet')]
    for filename in ('scores.jsonl', 'responses.jsonl', 'manifest.json', 'source_runs.json', 'prompts.snapshot.json'):
        if (output / filename).exists():
            links.append((filename, filename))
    for source in sources:
        for filename in ('responses.jsonl', 'scores.jsonl', 'manifest.json'):
            path = Path(source.get('path', output)) / filename
            if path.exists() and path.parent.resolve() != output.resolve():
                links.append((quote(os.path.relpath(path, output)), source.get('label', '') + '/' + filename))
    parts.append('<section id="sources"><h2>Source scope, reproducibility, and human review</h2>' + _table(
        ['Source', 'Original status', 'Original recorded attempts', 'Original plan', 'Directory'], source_cells) +
        '<p>' + E(manifest.get('selection_note', 'Source manifests preserve their original study scope.')) + ' ' +
        E(f"Excluded source observations: {manifest.get('excluded_source_observations', 0)}.") + '</p><p>' +
        ' · '.join(f'<a href="{E(url)}">{E(label)}</a>' for url, label in links) + '</p>'
        '<p>Human scores are never copied from automatic labels. Two independent raters can fill review.csv; existing ratings survive regeneration. '
        'The purposive English-language synthetic prompts and automatic detectors do not establish population-wide safety, usability, or model superiority. '
        'No overall composite score or statistical significance claim is made.</p></section>')
    style = ('body{font:16px/1.6 system-ui,sans-serif;max-width:1360px;margin:40px auto;padding:0 24px;color:#172033;background:#f7f9fc}'
             'h1{font-size:36px}h2{margin-top:44px}nav{padding:16px;background:#e9eff7;border-radius:12px}a{color:#175cc6}'
             '.status{font-weight:700}.table{overflow-x:auto}table{border-collapse:collapse;width:100%;background:white;font-size:13px}'
             'th,td{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #dce2e8;overflow-wrap:anywhere}th{background:#e9eff7}'
             'details{margin:12px 0;padding:12px 16px;background:white;border:1px solid #dce2e8;border-radius:8px}summary{cursor:pointer;font-weight:650}'
             'pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:14px;background:#f0f4fa;font:13px/1.55 ui-monospace,monospace}section{scroll-margin-top:20px}')
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>LLM interaction evaluation — detailed report</title><style>' + style + '</style><body>' + ''.join(parts) + '</body></html>'
    (output / 'report.html').write_text(page, encoding='utf-8')
    return summary


def generate_comparison(output, run_dirs, selected_models=None, selected_items=None):
    """Derive a cohort report without changing either source plan or manifest."""
    output, directories = Path(output), [Path(directory).resolve() for directory in run_dirs]
    if not directories or output.resolve() in directories:
        raise ValueError('Use a separate output directory and at least one source run')
    rows, sources, config, suite, known, common, references = [], [], None, None, set(), None, {}
    keys = ('repeats', 'temperature', 'num_predict', 'num_ctx', 'seed', 'condition', 'scorer_version')
    for directory in directories:
        manifest = _json(directory / 'manifest.json', {})
        frozen = _json(directory / 'prompts.snapshot.json', {})
        current = manifest.get('config', {})
        if config is not None and any(current.get(key) != config.get(key) for key in keys):
            raise ValueError('Source measurement settings differ; do not pool them')
        current_items = {item['id']: item for item in frozen.get('items', [])}
        for identity, item in current_items.items():
            if identity in references and references[identity] != item:
                raise ValueError('Shared source prompt content or rubric differs: ' + identity)
            references[identity] = item
        common = set(current_items) if common is None else common & set(current_items)
        config, suite = current, frozen
        raw = (directory / 'scores.jsonl').read_bytes() if (directory / 'scores.jsonl').exists() else b''
        loaded = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
        if len({str(row['row_id']) for row in loaded}) != len(loaded):
            raise ValueError('Duplicate source row_id')
        source_id = hashlib.sha256(str(directory).encode()).hexdigest()[:10]
        sources.append({'label': directory.name, 'path': str(directory), 'manifest': manifest,
                        'scores_sha256': hashlib.sha256(raw).hexdigest(), 'inventory': _json(directory / 'inventory.json', {})})
        models = current.get('models', manifest.get('models', []))
        known.update(model.get('name', '') if isinstance(model, dict) else model for model in models)
        rows.extend(dict(row, source_run=directory.name, source_row_id=row['row_id'], row_id=source_id + ':' + str(row['row_id'])) for row in loaded)
    selected = set(selected_models) if selected_models is not None else known
    if selected - known:
        raise ValueError('Selected model is absent from the frozen source plans')
    item_ids = set(selected_items) if selected_items is not None else common
    if not item_ids or item_ids - common:
        raise ValueError('Selected prompts must exist identically in every source snapshot')
    suite = dict(suite, items=[references[identity] for identity in sorted(item_ids)])
    config = dict(config, suite_sha256=hashlib.sha256(json.dumps(suite, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
    observed = [row for row in rows if row['model'] in selected and row['item_id'] in item_ids]
    expected = {(model, item['id'], repeat) for model in selected for item in suite.get('items', [])
                for repeat in range(1, int(config.get('repeats', 3)) + 1)}
    identities = [(row['model'], row['item_id'], int(row['repeat'])) for row in observed]
    if len(set(identities)) != len(identities) or set(identities) - expected:
        raise ValueError('Duplicate or out-of-plan selected model/item/repeat cells')
    merged = {'status': 'complete' if set(identities) == expected else 'partial', 'planned_jobs': len(expected),
              'completed_jobs': len(observed), 'config': dict(config, models=[{'name': name} for name in sorted(selected)]),
              'source_runs': sources, 'excluded_source_observations': len(rows) - len(observed),
              'selection_note': f'This report evaluates only {len(selected)} selected models and {len(item_ids)} shared prompts. Earlier broader source plans may remain partial after the user narrowed the cohort and prompt battery; their original manifests are unchanged.'}
    output.mkdir(parents=True, exist_ok=True)
    for filename, value in [('manifest.json', merged), ('source_runs.json', sources), ('prompts.snapshot.json', suite)]:
        (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'scores.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in observed), encoding='utf-8')
    return generate(output, observed, merged, suite)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', action='append', help='Include only these installed model names')
    parser.add_argument('--item', action='append', help='Include only these shared prompt IDs')
    arguments = parser.parse_args()
    generate_comparison(arguments.output, arguments.source, arguments.model, arguments.item)
    print(arguments.output / 'report.html')
