"""Compatible study cells can be joined without changing source observations."""

import copy
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from benchmark import CONDITIONS, canonical_hash, read_jsonl, write_csv, write_json
from combine_runs import combine


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')


def test_item(item_id):
    return {'id': item_id, 'category': 'control', 'research_role': 'answerable_control',
            'prompt': f'Return 17 for {item_id}.', 'rubric': 'Return the exact integer.',
            'scoring': {'type': 'exact', 'expected': ['17']}}


class CompatibleRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / 'baseline'
        self.focus = self.root / 'focused'
        self.output = self.root / 'analysis'
        self.config = {'repeats': 2, 'seed': 123, 'temperature': .5, 'num_predict': 512,
                       'num_ctx': 4096, 'timeout': 180, 'keep_alive': '2m', 'think': False,
                       'generation_source_sha256': 'fixture-generation',
                       'thinking_by_model': {'fixture': None},
                       'models': [{'name': 'fixture', 'digest': 'fixture-digest'}]}
        self.make_run(self.base, ['a', 'b'], ['baseline'])
        self.make_run(self.focus, ['a'], ['structured', 'self_check'])

    def make_run(self, path, item_ids, conditions):
        path.mkdir()
        suite = {'version': 3, 'items': [test_item(item_id) for item_id in item_ids]}
        config = {**copy.deepcopy(self.config), 'conditions': conditions,
                  'suite_sha256': canonical_hash(suite)}
        rows = []
        for item_id in item_ids:
            for condition in conditions:
                for repeat in (1, 2):
                    rows.append({'row_id': f'{path.name}-{item_id}-{condition}-{repeat}',
                                 'model': 'fixture', 'model_digest': 'fixture-digest',
                                 'item_id': item_id, 'category': 'control', 'condition': condition,
                                 'repeat': repeat, 'seed': 100 + repeat, 'expected_turns': 1,
                                 'completed_turns': 1, 'reply': '17', 'prompt': test_item(item_id)['prompt'],
                                 'seconds': .25, 'error': '', 'done_reason': 'stop', 'turns': []})
        manifest = {'run_id': path.name, 'status': 'complete', 'config': config,
                    'suite_sha256': canonical_hash(suite), 'planned_jobs': len(rows),
                    'completed_jobs': len(rows), 'planned_requests': len(rows),
                    'scorer_version': '2.0', 'scoring_source_sha256': 'fixture-scorer',
                    'ollama_version': 'fixture-ollama', 'models': config['models']}
        write_json(path / 'manifest.json', manifest)
        write_json(path / 'prompts.snapshot.json', suite)
        write_json(path / 'conditions.json', CONDITIONS)
        write_json(path / 'inventory.json', {'version': 'fixture-ollama', 'models': config['models']})
        write_json(path / 'scoring_provenance.json', {'scorer_version': '2.0', 'source_sha256': 'fixture-scorer'})
        write_jsonl(path / 'responses.jsonl', rows)
        write_jsonl(path / 'scores.jsonl', [{**row, 'scorer_version': '2.0', 'auto_pass': 1} for row in rows])
        plan_fields = ['row_id', 'model', 'model_digest', 'item_id', 'category', 'condition',
                       'repeat', 'seed', 'expected_turns']
        write_csv(path / 'plan.csv', rows, plan_fields)
        return rows

    def update_manifest(self, path, update):
        target = path / 'manifest.json'
        manifest = json.loads(target.read_text())
        update(manifest)
        write_json(target, manifest)

    def source_fingerprints(self):
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for source in (self.base, self.focus) for path in source.iterdir() if path.is_file()}

    def test_union_keeps_source_scope_and_does_not_invent_strategy_cells(self):
        before = self.source_fingerprints()
        result = combine([self.base, self.focus], self.output)
        self.assertTrue(result['analysis_only'])
        self.assertEqual(result['planned_jobs'], 8)
        self.assertEqual(result['planned_requests'], 8)
        self.assertEqual(result['condition_item_counts'], {'baseline': 2, 'structured': 1, 'self_check': 1})
        rows = read_jsonl(self.output / 'responses.jsonl')
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(row['item_id'] == 'a' for row in rows if row['condition'] != 'baseline'))
        self.assertEqual({row['source_run_id'] for row in rows}, {'baseline', 'focused'})
        self.assertTrue(all(Path(row['source_run_path']) in {self.base.resolve(), self.focus.resolve()} for row in rows))
        self.assertEqual(self.source_fingerprints(), before)
        # Rebuilding the same analysis is deterministic and remains non-destructive.
        again = combine([self.base, self.focus], self.output)
        self.assertEqual(result, again)
        self.assertEqual(self.source_fingerprints(), before)

    def test_changed_parameters_or_model_digests_refused(self):
        for field, changed in (('temperature', .7), ('seed', 999),
                               ('models', [{'name': 'fixture', 'digest': 'different-weights'}]),
                               ('generation_source_sha256', 'different-code')):
            with self.subTest(field=field):
                original = (self.focus / 'manifest.json').read_bytes()
                self.update_manifest(self.focus, lambda m: m['config'].__setitem__(field, changed))
                with self.assertRaisesRegex(ValueError, 'Generation settings/model digests differ'):
                    combine([self.base, self.focus], self.output)
                (self.focus / 'manifest.json').write_bytes(original)

    def test_changed_shared_prompt_rejected_even_with_updated_suite_hash(self):
        suite_path = self.focus / 'prompts.snapshot.json'
        suite = json.loads(suite_path.read_text())
        suite['items'][0]['prompt'] = 'Different treatment for the same item ID.'
        write_json(suite_path, suite)
        def update(manifest):
            manifest['suite_sha256'] = canonical_hash(suite)
            manifest['config']['suite_sha256'] = canonical_hash(suite)
        self.update_manifest(self.focus, update)
        with self.assertRaisesRegex(ValueError, 'Shared prompt/rubric differs'):
            combine([self.base, self.focus], self.output)

    def test_tampered_snapshot_rejected_without_rehash(self):
        suite_path = self.focus / 'prompts.snapshot.json'
        suite = json.loads(suite_path.read_text())
        suite['items'][0]['scoring']['expected'] = ['18']
        write_json(suite_path, suite)
        with self.assertRaisesRegex(ValueError, 'snapshot was changed'):
            combine([self.base, self.focus], self.output)

    def test_manifest_scorer_or_serving_version_mismatch_refused(self):
        for field in ('scorer_version', 'scoring_source_sha256', 'ollama_version'):
            with self.subTest(field=field):
                original = (self.focus / 'manifest.json').read_bytes()
                self.update_manifest(self.focus, lambda m: m.__setitem__(field, 'different'))
                with self.assertRaisesRegex(ValueError, 'Scorer or Ollama version differs'):
                    combine([self.base, self.focus], self.output)
                (self.focus / 'manifest.json').write_bytes(original)

    def test_actual_score_versions_and_provenance_must_match_manifest(self):
        provenance_path = self.focus / 'scoring_provenance.json'
        original = provenance_path.read_bytes()
        write_json(provenance_path, {'scorer_version': '1.0', 'source_sha256': 'fixture-scorer'})
        with self.assertRaisesRegex(ValueError, 'scoring provenance disagrees'):
            combine([self.base, self.focus], self.output)
        provenance_path.write_bytes(original)
        scores_path = self.focus / 'scores.jsonl'
        rows = read_jsonl(scores_path)
        rows[0]['scorer_version'] = '1.0'
        write_jsonl(scores_path, rows)
        with self.assertRaisesRegex(ValueError, 'different measurement versions'):
            combine([self.base, self.focus], self.output)

    def test_missing_score_rows_are_not_silently_ignored(self):
        scores_path = self.focus / 'scores.jsonl'
        write_jsonl(scores_path, read_jsonl(scores_path)[:-1])
        with self.assertRaisesRegex(ValueError, 'score rows are missing'):
            combine([self.base, self.focus], self.output)

    def test_duplicate_experimental_cells_rejected_even_with_distinct_row_ids(self):
        duplicate = self.root / 'duplicated-baseline'
        self.make_run(duplicate, ['a'], ['baseline'])
        with self.assertRaisesRegex(ValueError, 'Duplicate observed experimental cell'):
            combine([self.base, duplicate], self.output)

    def test_partial_or_falsely_complete_manifest_refused(self):
        original = (self.focus / 'manifest.json').read_bytes()
        self.update_manifest(self.focus, lambda m: m.__setitem__('status', 'partial'))
        with self.assertRaisesRegex(ValueError, 'only completed source runs'):
            combine([self.base, self.focus], self.output)
        (self.focus / 'manifest.json').write_bytes(original)
        raw = self.focus / 'responses.jsonl'
        write_jsonl(raw, read_jsonl(raw)[:-1])
        with self.assertRaisesRegex(ValueError, 'committed rows do not match'):
            combine([self.base, self.focus], self.output)

    def test_paired_seed_consistency_is_checked_against_observed_rows(self):
        raw = self.focus / 'responses.jsonl'
        rows = read_jsonl(raw)
        rows[0]['seed'] = 999
        write_jsonl(raw, rows)
        with (self.focus / 'plan.csv').open(newline='') as handle:
            plan = list(csv.DictReader(handle))
        plan[0]['seed'] = '999'
        write_csv(self.focus / 'plan.csv', plan, list(plan[0]))
        with self.assertRaisesRegex(ValueError, 'different random seeds'):
            combine([self.base, self.focus], self.output)

    def test_strategy_wrapper_metadata_mismatch_refused(self):
        write_json(self.focus / 'conditions.json', {**CONDITIONS, 'structured': 'Changed treatment'})
        with self.assertRaisesRegex(ValueError, 'prompt-strategy text differs'):
            combine([self.base, self.focus], self.output)

    def test_empty_sources_and_replacing_source_directory_refused(self):
        with self.assertRaisesRegex(ValueError, 'cannot replace a source run'):
            combine([self.base, self.focus], self.base)
        raw = self.focus / 'responses.jsonl'
        raw.write_text('')
        self.update_manifest(self.focus, lambda m: m.__setitem__('planned_jobs', 0))
        with self.assertRaisesRegex(ValueError, 'Empty sources'):
            combine([self.base, self.focus], self.output)

    def test_different_existing_output_is_not_replaced(self):
        self.output.mkdir()
        write_json(self.output / 'manifest.json', {'status': 'complete', 'analysis_only': False})
        with self.assertRaisesRegex(ValueError, 'Refusing to replace a different run'):
            combine([self.base, self.focus], self.output)


if __name__ == '__main__':
    unittest.main()
