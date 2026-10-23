"""Isolation and orchestration tests use fake processes and sockets, never Ollama."""

import contextlib
import io
import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch

import research


class FakeServer:
    """A process handle that records cleanup without affecting real processes."""
    def __init__(self):
        self.pid = 97531
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class ResearchOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.prefix = Path(self.temporary.name) / 'study'
        self.server = FakeServer()
        self.commands = []
        self.inventory = {'version': 'fixture', 'models': [
            {'name': name, 'eligible': True, 'digest': f'digest-{index}'}
            for index, name in enumerate(research.MODELS)]}
        self.client = Mock()
        self.client._json.return_value = {'version': 'fixture'}
        self.client.inventory.side_effect = lambda: self.inventory
        self.patchers = [
            patch.object(research.shutil, 'which', return_value='/fixture/ollama'),
            patch.object(research.socket, 'socket'),
            patch.object(research.subprocess, 'Popen', return_value=self.server),
            patch.object(research, 'OllamaClient', return_value=self.client),
            patch.object(research, 'protected_state', return_value={'models': [{'name': 'qwen3-coder:latest'}]}),
            patch.object(research, 'run_command', side_effect=lambda command, environment: self.commands.append((command, environment))),
        ]
        self.mocks = [patcher.start() for patcher in self.patchers]
        for patcher in self.patchers:
            self.addCleanup(patcher.stop)
        self.mocks[1].return_value.__enter__.return_value.connect_ex.return_value = 1

    def run_main(self, *options):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return research.main(['--run-prefix', str(self.prefix), *options])

    def test_default_only_prepares_and_cleans_up_owned_server(self):
        self.assertEqual(self.run_main(), 0)
        runner_commands = [command for command, _ in self.commands if command[1] == 'run_tests.py']
        self.assertEqual(len(runner_commands), 2)
        self.assertTrue(all('--execute' not in command for command in runner_commands))
        self.assertFalse(any(command[1] == 'combine_runs.py' for command, _ in self.commands))
        self.assertTrue(self.server.terminated)
        self.assertFalse(self.server.killed)
        args, kwargs = self.mocks[2].call_args
        self.assertEqual(args[0], ['ollama', 'serve'])
        self.assertEqual(kwargs['env']['OLLAMA_HOST'], '127.0.0.1:11435')
        self.assertEqual(kwargs['env']['OLLAMA_MAX_LOADED_MODELS'], '1')
        self.assertEqual(kwargs['env']['OLLAMA_NOPRUNE'], '1')
        self.assertEqual(kwargs['env']['OLLAMA_NO_CLOUD'], '1')
        self.assertNotIn('qwen3-coder:latest', [part for command in runner_commands for part in command])
        audit = json.loads((self.prefix / 'coexistence.json').read_text())
        self.assertEqual(audit['status'], 'prepared')
        self.assertTrue(audit['temporary_server_stopped'])
        self.assertEqual(audit['temporary_server_pid'], self.server.pid)
        self.assertEqual(self.client._json.call_args_list[0].args, ('/api/version',))
        self.client.chat.assert_not_called()

    def test_execute_is_explicit_and_combines_only_after_both_stages(self):
        self.assertEqual(self.run_main('--execute'), 0)
        commands = [command for command, _ in self.commands]
        runner_indices = [index for index, command in enumerate(commands) if command[1] == 'run_tests.py']
        combine_index = next(index for index, command in enumerate(commands) if command[1] == 'combine_runs.py')
        self.assertTrue(all('--execute' in commands[index] for index in runner_indices))
        self.assertGreater(combine_index, max(runner_indices))
        self.assertEqual(commands[runner_indices[0]][commands[runner_indices[0]].index('--conditions') + 1], 'baseline')
        audit = json.loads((self.prefix / 'coexistence.json').read_text())
        self.assertEqual(audit['status'], 'complete')

    def test_primary_port_and_invalid_repeat_count_are_rejected_before_process_start(self):
        for options in (('--port', '11434'), ('--repeats', '1'), ('--port', '80')):
            with self.subTest(options=options), self.assertRaises(SystemExit):
                self.run_main(*options)
        self.mocks[2].assert_not_called()
        self.mocks[4].assert_not_called()

    def test_framework_is_one_fresh_baseline_stage_with_protocol_repeats(self):
        self.assertEqual(self.run_main('--framework', '--execute'), 0)
        commands = [command for command, _ in self.commands]
        runners = [command for command in commands if command[1] == 'run_tests.py']
        self.assertEqual(len(runners), 1)
        runner = runners[0]
        self.assertEqual(runner[runner.index('--run-dir') + 1], str(self.prefix))
        self.assertEqual(runner[runner.index('--prompts') + 1], 'prompts.framework.json')
        self.assertEqual(runner[runner.index('--conditions') + 1], 'baseline')
        self.assertIn('--execute', runner)
        self.assertFalse(any(command[1] == 'combine_runs.py' for command in commands))
        audit = json.loads((self.prefix / 'coexistence.json').read_text())
        self.assertEqual(audit['protocol'], 'framework-v1')
        self.assertTrue(audit['temporary_server_stopped'])

    def test_framework_rejects_repeats_outside_document_protocol(self):
        for repeats in ('2', '6'):
            with self.subTest(repeats=repeats), self.assertRaises(SystemExit):
                self.run_main('--framework', '--repeats', repeats)
        self.mocks[2].assert_not_called()

    def test_existing_port_is_not_attached_to_or_terminated(self):
        self.mocks[1].return_value.__enter__.return_value.connect_ex.return_value = 0
        with self.assertRaises(SystemExit):
            self.run_main()
        self.mocks[2].assert_not_called()
        self.assertFalse(self.server.terminated)
        self.mocks[4].assert_not_called()

    def test_missing_model_fails_and_stops_only_owned_process(self):
        self.inventory['models'].pop()
        self.assertEqual(self.run_main('--execute'), 1)
        self.assertEqual(self.commands, [])
        self.assertTrue(self.server.terminated)
        self.mocks[2].assert_called_once()
        audit = json.loads((self.prefix / 'coexistence.json').read_text())
        self.assertEqual(audit['status'], 'failed')
        self.assertIn('unavailable', audit['error'])

    def test_failed_stage_is_audited_and_owned_server_stopped(self):
        self.mocks[5].side_effect = RuntimeError('fixture stage failed')
        self.assertEqual(self.run_main('--execute'), 1)
        self.assertTrue(self.server.terminated)
        audit = json.loads((self.prefix / 'coexistence.json').read_text())
        self.assertEqual(audit['status'], 'failed')
        self.assertIn('fixture stage failed', audit['error'])

    def test_protected_state_uses_only_read_only_ps_endpoint(self):
        # Exercise the real helper with a mocked client, rather than our usual
        # patched helper, to verify that no chat/keep-alive request is possible.
        self.patchers[4].stop()
        factory = Mock()
        factory.return_value._json.return_value = {'models': []}
        with patch.object(research, 'OllamaClient', factory):
            self.assertEqual(research.protected_state(), {'models': []})
        factory.assert_called_once_with('http://127.0.0.1:11434', timeout=3)
        factory.return_value._json.assert_called_once_with('/api/ps')
        factory.return_value.chat.assert_not_called()


class StageProcessTests(unittest.TestCase):
    def test_interrupt_is_forwarded_to_exact_owned_child(self):
        child = Mock()
        child.wait.side_effect = [KeyboardInterrupt(), 130]
        with patch.object(research.subprocess, 'Popen', return_value=child), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            research.run_command(['fixture-stage'], {})
        child.send_signal.assert_called_once_with(signal.SIGINT)
        child.terminate.assert_not_called()

    def test_nonzero_stage_does_not_look_successful(self):
        child = Mock()
        child.wait.return_value = 3
        with patch.object(research.subprocess, 'Popen', return_value=child), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'exit code 3'):
            research.run_command(['fixture-stage'], {})


if __name__ == '__main__':
    unittest.main()
