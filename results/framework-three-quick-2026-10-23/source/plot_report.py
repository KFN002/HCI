"""Optional Matplotlib figures for saved framework measurements; no model calls."""

import csv
import json
import statistics
from pathlib import Path

import reporting


DIMENSIONS = [('clarification', 'Clarification rubric', 1, 5),
              ('negative_constraint', 'Negative adherence', 0, 1),
              ('epistemic_honesty', 'Epistemic honesty', 0, 2),
              ('efficiency', 'Filler-free character ratio (ER proxy)', 0, 1)]
COLORS = ['#2459a6', '#cb7925', '#1d8068', '#8656a3', '#b84e60']
CATEGORIES = {'ambiguity': 'Ambiguity', 'negative_constraints': 'Negative\nconstraints',
              'prompt_injection': 'Prompt\ninjection', 'security_boundaries': 'Security\nboundaries',
              'creative_formatting': 'Creative\nformatting', 'sensitive_topics': 'Sensitive\ntopics'}


def short_label(model):
    name = model.lower()
    if 'caption' in name:
        return 'Qwen2.5-VL Caption 7B'
    if 'qwen3.8-27b' in name:
        return 'Qwen3.8 27B'
    for family in ('gemma3', 'qwen2.5', 'qwen3', 'granite4', 'llama3.2', 'phi4-mini'):
        if name.startswith(family + ':'):
            return family.replace('qwen', 'Qwen').replace('gemma', 'Gemma').replace('granite', 'Granite').replace('llama', 'Llama').replace('phi', 'Phi') + ' ' + model.split(':', 1)[1].upper()
    tail = model.rsplit('/', 1)[-1]
    return tail if len(tail) <= 34 else tail[:31] + '…'


def build(output, rows, summary):
    """Export SVG/PNG figures and exact source values, or explain a missing dependency."""
    if not summary:
        return []
    directory = Path(output) / 'charts'
    directory.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        (directory / 'status.json').write_text(json.dumps(dict(available=False, instruction=
            'Install matplotlib>=3.11 in the reporting Python environment, then regenerate the detailed report. The CSVs and text report remain usable.')) + '\n')
        return []
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11, 'axes.titlesize': 14,
                         'axes.spines.top': False, 'axes.spines.right': False, 'axes.spines.left': False,
                         'axes.edgecolor': '#b6c2d2', 'axes.labelcolor': '#23334b', 'text.color': '#23334b',
                         'xtick.color': '#53647b', 'ytick.color': '#23334b', 'svg.fonttype': 'none', 'svg.hashsalt': 'hci-framework-v1'})
    names, palette = [short_label(row['model']) for row in summary], [COLORS[i % len(COLORS)] for i in range(len(summary))]
    index = []

    def save(fig, filename, title, caption):
        fig.savefig(directory / (filename + '.svg'), bbox_inches='tight', metadata={'Date': None})
        fig.savefig(directory / (filename + '.png'), bbox_inches='tight', dpi=150)
        plt.close(fig)
        index.append(dict(file='charts/' + filename + '.svg', png='charts/' + filename + '.png',
                          title=title, caption=caption, data='charts/chart_data.csv'))

    def panels(variance=False):
        fig, axes = plt.subplots(2, 2, figsize=(18, 9), layout='constrained')
        fig.suptitle('Within-prompt score variance' if variance else 'Four operational dimensions', fontsize=21, fontweight='bold')
        for axis, (dimension, title, low, high) in zip(axes.flat, DIMENSIONS):
            field = dimension + ('_within_prompt_variance' if variance else '_mean')
            if variance:
                low, high = 0, max((row[field] for row in summary if row[field] != ''), default=0) * 1.25 or 1
            axis.set_title(title, loc='left', pad=18)
            axis.set_xlim(low, high)
            axis.set_yticks(range(len(names)), names)
            axis.set_ylim(len(names) - .4, -.6)
            axis.set_axisbelow(True)
            axis.grid(axis='x', color='#e2e8f0')
            axis.set_xlabel('Mean sample variance (squared score units)' if variance else f'Observed mean; scale {low}–{high}')
            if not variance:
                axis.set_xticks(np.linspace(low, high, 5))
                if dimension == 'negative_constraint':
                    axis.set_xticklabels([f'{tick:.0%}' for tick in np.linspace(low, high, 5)])
            for position, row in enumerate(summary):
                value = row[field]
                if value == '':
                    shown = 'pending'
                else:
                    axis.barh(position, float(value) - low, left=low, height=.48, color=palette[position])
                    axis.scatter([float(value)], [position], color=palette[position], s=24, zorder=3)
                    shown = f'{value:.3g}' if variance else f'{value:.1%}' if dimension == 'negative_constraint' else f'{value:.2f}'
                count = (f"{row[dimension+'_repeat_prompts']} prompts / {row[dimension+'_repeat_observations']} repeats" if variance
                         else f"n/N {row[dimension+'_observed']}/{row[dimension+'_eligible']}")
                axis.text(1.025, position, shown + '\n' + count, transform=axis.get_yaxis_transform(), va='center', fontsize=10)
        return fig

    save(panels(), 'metrics', 'Four metric comparisons',
         'Independent scales; n/N = scored / eligible. Epistemic means describe only the scored subset; unresolved answers can include errors and correct paraphrases. ER is a filler parser proxy.')
    fig, axis = plt.subplots(figsize=(15, max(6, len(summary) * 2.1)), layout='constrained')
    tick_labels = []
    for model_index, row in enumerate(summary):
        for metric_index, (dimension, title, _, _) in enumerate(DIMENSIONS):
            position = model_index * 5 + metric_index
            n, eligible = row[dimension + '_observed'], row[dimension + '_eligible']
            axis.barh(position, 1, color='#e7edf5', height=.68)
            if eligible:
                axis.barh(position, n / eligible, color=palette[model_index], height=.68)
            axis.text(1.02, position, f'{n}/{eligible}; {eligible-n} unscored' if eligible else 'not applicable', va='center', fontsize=10)
            tick_labels.append((position, names[model_index] + ' · ' + title.replace('Filler-free character ratio (ER proxy)', 'ER proxy')))
    axis.set_yticks([position for position, _ in tick_labels], [label for _, label in tick_labels])
    axis.invert_yaxis()
    axis.set_xlim(0, 1)
    axis.set_xticks(np.linspace(0, 1, 6), [f'{tick:.0%}' for tick in np.linspace(0, 1, 6)])
    axis.set_title('Score coverage and missingness', loc='left', fontsize=20, fontweight='bold', pad=20)
    axis.set_xlabel('Scored replies / eligible recorded attempts')
    save(fig, 'coverage', 'Coverage and missingness', 'Grey remainder = unscored eligible attempts, including errors, limits, or unresolved judgments. Read this before conditional means.')
    categories = sorted({row['category'] for row in rows})
    fig, axis = plt.subplots(figsize=(15, max(4.8, len(summary) * 1.1)), layout='constrained')
    if categories:
        cells = [[reporting.summarize([row for row in rows if row['model'] == model['model'] and row['category'] == category], model['model'])
                  for category in categories] for model in summary]
        array = np.array([[cell['negative_constraint_mean'] if cell['negative_constraint_mean'] != '' else np.nan for cell in line] for line in cells])
        cmap = plt.get_cmap('YlGnBu').with_extremes(bad='#e7edf5')
        image = axis.imshow(np.ma.masked_invalid(array), vmin=0, vmax=1, cmap=cmap, aspect='auto')
        axis.set_yticks(range(len(names)), names)
        axis.set_xticks(range(len(categories)), [CATEGORIES.get(category, category.replace('_', '\n')) for category in categories])
        for y, line in enumerate(cells):
            for x, cell in enumerate(line):
                value, n, eligible = cell['negative_constraint_mean'], cell['negative_constraint_observed'], cell['negative_constraint_eligible']
                label = ('pending' if value == '' else f'{value:.0%}') + f'\nn/N {n}/{eligible}'
                axis.text(x, y, label, ha='center', va='center', color='white' if value != '' and value > .65 else '#23334b', fontsize=11)
        fig.colorbar(image, ax=axis, fraction=.025, pad=.025, label='Passing declared prohibitions / scored replies')
    else:
        axis.text(.5, .5, 'No category observations yet', ha='center', transform=axis.transAxes)
    axis.set_title('Negative adherence by category', loc='left', fontsize=20, fontweight='bold', pad=20)
    save(fig, 'negative_categories', 'Negative adherence by category', 'Only declared prohibitions are checked; grey cells have no scored replies. n/N exposes measurement coverage.')
    save(panels(variance=True), 'repeat_variance', 'Repeated-prompt variance',
         'Mean within-prompt sample variance across prompts with at least two scored repeats. Low variance measures stability, not correctness; no confidence intervals are implied.')
    latency = [dict(row_id=row['row_id'], model=row['model'], label=short_label(row['model']),
                    seconds=value, truncated=reporting._truncated(row)) for row in rows
               if row.get('reply') and not row.get('error')
               if (value := reporting._number(row.get('seconds'))) is not None and value >= 0]
    fig, axis = plt.subplots(figsize=(15, 4.8), layout='constrained')
    for position, model in enumerate(summary):
        durations = [row['seconds'] for row in latency if row['model'] == model['model']]
        if durations:
            axis.boxplot([durations], positions=[position], orientation='horizontal', widths=.5, patch_artist=True,
                         boxprops=dict(facecolor=palette[position], alpha=.7), medianprops=dict(color='#172033', linewidth=2),
                         flierprops=dict(marker='o', markersize=4, markerfacecolor=palette[position], markeredgecolor='none'))
            axis.text(1.02, position, f'n={len(durations)}; median {statistics.median(durations):.2f}s', transform=axis.get_yaxis_transform(), va='center', fontsize=10)
        else:
            axis.text(1.02, position, 'pending', transform=axis.get_yaxis_transform(), va='center')
    axis.set_yticks(range(len(names)), names)
    axis.invert_yaxis()
    axis.set_xlim(left=0)
    axis.set_xlabel('Wall-clock response seconds; returned replies including truncated outputs')
    axis.set_title('Observed response latency', loc='left', fontsize=20, fontweight='bold', pad=20)
    save(fig, 'latency', 'Observed response latency', 'Box = interquartile range; center = median; whiskers extend to 1.5 IQR. Different sizes/default thinking make this unequal-compute timing evidence.')
    index[-1]['data'] = 'charts/latency_data.csv'
    with (directory / 'latency_data.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, ['row_id', 'model', 'label', 'seconds', 'truncated'])
        writer.writeheader()
        writer.writerows(latency)
    data = []
    for model in summary:
        for category in ['ALL'] + categories:
            cell = model if category == 'ALL' else reporting.summarize([
                row for row in rows if row['model'] == model['model'] and row['category'] == category], model['model'])
            for dimension in reporting.DIMENSIONS:
                data.append(dict(model=cell['model'], label=short_label(cell['model']), category=category, metric=dimension,
                                 value=cell[dimension + '_mean'], observed=cell[dimension + '_observed'], eligible=cell[dimension + '_eligible'],
                                 coverage=cell[dimension + '_coverage'], within_prompt_variance=cell[dimension + '_within_prompt_variance'],
                                 repeated_prompts=cell[dimension + '_repeat_prompts'], repeated_observations=cell[dimension + '_repeat_observations']))
    with (directory / 'chart_data.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, list(data[0]))
        writer.writeheader()
        writer.writerows(data)
    (directory / 'index.json').write_text(json.dumps(index, indent=2) + '\n', encoding='utf-8')
    (directory / 'status.json').write_text(json.dumps(dict(available=True, renderer='matplotlib', version=matplotlib.__version__)) + '\n')
    return index
