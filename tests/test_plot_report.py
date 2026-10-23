"""Chart exports retain score scales, missingness, and auditable source values."""

import csv
import importlib.util
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import plot_report
import reporting


class Labels(unittest.TestCase):
    def test_readable_labels_keep_model_variants_distinct(self):
        self.assertEqual(plot_report.short_label('gemma3:4b'), 'Gemma3 4B')
        self.assertEqual(plot_report.short_label('hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M'), 'Qwen3.8 27B')
        self.assertEqual(plot_report.short_label('hf.co/bartowski/thesby_Qwen2.5-VL-7B-NSFW-Caption-V3-GGUF:Q4_K_M'), 'Qwen2.5-VL Caption 7B')

    def test_empty_study_has_no_invented_figures(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(plot_report.build(temporary, [], []), [])
            self.assertFalse((Path(temporary) / 'charts').exists())


@unittest.skipUnless(importlib.util.find_spec('matplotlib'), 'optional matplotlib renderer unavailable')
class Figures(unittest.TestCase):
    def test_figures_are_valid_svg_png_and_retain_missing_denominators(self):
        rows = [dict(row_id='a', model='gemma3:4b', item_id='amb1', category='ambiguity', repeat=1,
                     reply='Which dates?', error='', seconds=2, clarification_applicable=1, clarification_score=3,
                     negative_constraint_applicable=1, negative_constraint_pass=1,
                     epistemic_honesty_applicable=1, epistemic_honesty_score=None,
                     efficiency_applicable=1, efficiency_ratio=1),
                dict(row_id='b', model='gemma3:4b', item_id='amb1', category='ambiguity', repeat=2,
                     reply='Which dates?', error='', seconds=4, clarification_applicable=1, clarification_score=5,
                     negative_constraint_applicable=1, negative_constraint_pass=0,
                     epistemic_honesty_applicable=1, epistemic_honesty_score=None,
                     efficiency_applicable=1, efficiency_ratio=1)]
        summary = [reporting.summarize(rows, 'gemma3:4b'), reporting.summarize([], 'unmeasured')]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            index = plot_report.build(directory, rows, summary)
            self.assertEqual(len(index), 5)
            for chart in index:
                root = ET.fromstring((directory / chart['file']).read_text())
                self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
                self.assertEqual((directory / chart['png']).read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
            with (directory / 'charts/chart_data.csv').open() as stream:
                source = list(csv.DictReader(stream))
            epistemic = next(row for row in source if row['category'] == 'ALL' and row['model'] == 'gemma3:4b'
                             and row['metric'] == 'epistemic_honesty')
            self.assertEqual(epistemic['value'], '')
            self.assertEqual(epistemic['observed'], '0')
            self.assertEqual(epistemic['eligible'], '2')
            variance = next(row for row in source if row['category'] == 'ALL' and row['model'] == 'gemma3:4b'
                            and row['metric'] == 'clarification')
            self.assertEqual(float(variance['within_prompt_variance']), 2)
            with (directory / 'charts/latency_data.csv').open() as stream:
                latency = list(csv.DictReader(stream))
            self.assertEqual([float(row['seconds']) for row in latency], [2, 4])
            metric_svg = (directory / 'charts/metrics.svg').read_text()
            self.assertIn('pending', metric_svg)
            self.assertIn('n/N 0/2', metric_svg)
            self.assertIn('Filler-free character ratio (ER proxy)', metric_svg)
            self.assertEqual(json.loads((directory / 'charts/status.json').read_text())['renderer'], 'matplotlib')


if __name__ == '__main__':
    unittest.main()
