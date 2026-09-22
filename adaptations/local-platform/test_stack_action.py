"""Contract tests for the stack lifecycle Action template.

The bridge tests cover the executor side; these tests execute the *rendered* Action body
under Node with a minimal Deno shim, so the operator-visible wording and the read-only
`job=` reconcile path are verified against a simulated queue instead of being asserted.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from project_registry import render_stack_action

NODE = shutil.which('node')

SHIM = r'''
import { mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

class NotFound extends Error {}
const clock = { now: 1758000000000 };
Date.now = () => (clock.now += 60000);          // no real waiting, a bounded loop still ends
globalThis.setTimeout = (fn) => { fn(); return 0; };
globalThis.ARGS = JSON.parse(readFileSync(process.env.ARGS_JSON, 'utf8'));

const scripted = JSON.parse(process.env.SCRIPTED_RESPONSES || '[]');
const capture = { requests: [], responses_read: 0, resumed: false };
process.on('exit', () => process.stdout.write('CAPTURE ' + JSON.stringify(capture) + '\n'));
let attempt = 0;

globalThis.Deno = {
  errors: { NotFound },
  readTextFile: (path) => {
    if (path.includes('/responses/')) {
      capture.responses_read += 1;
      if (scripted.length) {
        const body = scripted[Math.min(attempt++, scripted.length - 1)];
        if (body === null) throw new NotFound(path);
        return JSON.stringify(body);
      }
    }
    try { return readFileSync(path, 'utf8'); }
    catch (error) { if (error.code === 'ENOENT') throw new NotFound(path); throw error; }
  },
  writeTextFile: (path, body) => { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, body); },
  rename: (from, to) => { capture.requests.push(JSON.parse(readFileSync(from, 'utf8'))); renameSync(from, to); },
  stat: (path) => { try { return statSync(path); } catch (error) { if (error.code === 'ENOENT') throw new NotFound(path); throw error; } },
};
'''


@unittest.skipIf(NODE is None, 'node is required to execute the Action contract tests')
class StackActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.queue = self.home / 'queue'
        for name in ('requests', 'responses', 'claims'):
            (self.queue / name).mkdir(parents=True, exist_ok=True)
        # The Action reads a heartbeat first: the shim's clock starts at a fixed instant.
        (self.queue / 'heartbeat.json').write_text(json.dumps({'pid': 1, 'protocol': 1, 'updated_at': 1758000000 + 86400}))
        body = render_stack_action('spec', 'status')
        marker = 'const queue = "/local-queue";'
        self.assertIn(marker, body, 'the Action template no longer declares its queue path as expected')
        body = body.replace(marker, 'const queue = ' + json.dumps(str(self.queue).replace('\\', '/')) + ';')
        self.action = self.home / 'action.mjs'
        self.action.write_text(SHIM + body, encoding='utf-8')

    def run_action(self, args, scripted=None):
        args_file = self.home / 'args.json'
        args_file.write_text(json.dumps(args))
        environment = dict(os.environ, ARGS_JSON=str(args_file),
                           SCRIPTED_RESPONSES=json.dumps(scripted if scripted is not None else []))
        result = subprocess.run([NODE, str(self.action)], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', env=environment, timeout=120)
        captured = None
        output = []
        for line in result.stdout.splitlines():
            if line.startswith('CAPTURE '):
                captured = json.loads(line[len('CAPTURE '):])
            else:
                output.append(line)
        return result.returncode, '\n'.join(output), result.stderr, captured

    def response(self, identity, **body):
        path = self.queue / 'responses' / (identity + '.json')
        result = dict(id=identity, kind='stack', project='spec')
        result.update(body)
        path.write_text(json.dumps(result))
        return path

    def test_submission_reports_progress_and_success(self):
        code, output, error, captured = self.run_action(
            {'operation': 'status'},
            scripted=[{'kind': 'stack', 'status': 'running', 'project': 'spec', 'stage': 'stack-status', 'output': ['docker compose ps']},
                      {'kind': 'stack', 'status': 'succeeded', 'project': 'spec', 'stage': 'done', 'output': ['docker compose ps']}])
        self.assertEqual(code, 0, output + error)
        self.assertEqual(len(captured['requests']), 1)
        request = captured['requests'][0]
        self.assertEqual(request['action'], 'stack')
        self.assertEqual(request['options'], {'operation': 'status', 'group': 'spec'})
        self.assertIn('阶段：stack-status', output)
        self.assertIn('docker compose ps', output)
        self.assertIn('status 完成', output)

    def test_executor_timeout_is_reported_as_unknown_not_as_failure(self):
        code, output, error, captured = self.run_action(
            {'operation': 'start'},
            scripted=[{'kind': 'stack', 'status': 'timed_out', 'project': 'spec', 'stage': 'timeout',
                       'error': 'Lifecycle operation exceeded 900s. The reported result is unknown AND the background '
                                'operation is still running.',
                       'output': [], 'execution': {'running': True, 'outcome': None, 'finished_at': None,
                                                   'deadline_guard': False, 'note': 'verdict reported while running'}}])
        identity = captured['requests'][0]['id']
        self.assertNotEqual(code, 0)
        self.assertIn('超时，结果未知', error)
        self.assertIn('job=' + identity, error, 'the operator must be told how to re-check')
        self.assertIn('900s', error)
        self.assertIn('后台仍在执行', error, 'the report must not imply the background work stopped')
        self.assertIn('核对容器状态', error)
        self.assertIn('后台执行：仍在执行', output)

    def test_a_late_outcome_after_the_timeout_verdict_is_surfaced(self):
        code, output, error, captured = self.run_action(
            {'operation': 'start'},
            scripted=[{'kind': 'stack', 'status': 'timed_out', 'project': 'spec', 'stage': 'timeout',
                       'error': 'Lifecycle operation exceeded 900s.', 'output': [],
                       'execution': {'running': False, 'outcome': 'succeeded', 'finished_at': 1.0,
                                     'deadline_guard': True, 'restarted': True,
                                     'error': 'Operation deadline reached; no further docker command was started',
                                     'note': 'the background operation has since finished'}}])
        identity = captured['requests'][0]['id']
        self.assertNotEqual(code, 0, 'the verdict is still unknown: it must not be reported as success')
        self.assertIn('后台已结束（实际结果 succeeded）', error)
        self.assertIn('job=' + identity, error)
        self.assertIn('后台执行：已结束，实际结果 succeeded（执行器重启过，结论需核对）', output)
        self.assertIn('后台执行错误：Operation deadline reached', output)

    def test_unanswered_operation_tells_the_operator_to_reconcile(self):
        code, output, error, captured = self.run_action({'operation': 'stop'}, scripted=[])
        identity = captured['requests'][0]['id']
        self.assertNotEqual(code, 0)
        self.assertIn('等待超时，结果未知', error)
        self.assertIn('job=' + identity, error)
        self.assertEqual(captured['requests'][0]['options']['operation'], 'stop')

    def test_job_argument_reconciles_without_submitting_anything(self):
        identity = 'b' * 32
        self.response(identity, status='succeeded', stage='done', output=['docker compose ps'])
        code, output, error, captured = self.run_action({'job': identity}, scripted=None)
        self.assertEqual(code, 0, output + error)
        self.assertEqual(captured['requests'], [], 'a reconcile read must never submit a lifecycle request')
        self.assertIn('回查', output)
        self.assertIn('完成', output)
        self.assertIn('b' * 32, output)

    def test_job_argument_reports_an_unknown_task(self):
        code, output, error, captured = self.run_action({'job': 'c' * 32}, scripted=None)
        self.assertNotEqual(code, 0)
        self.assertIn('未找到任务', error)
        self.assertEqual(captured['requests'], [])

    def test_job_argument_rejects_a_foreign_group_and_bad_shape(self):
        identity = 'd' * 32
        self.response(identity, status='succeeded', project='mqtt-sandbox', stage='done')
        code, output, error, _ = self.run_action({'job': identity}, scripted=None)
        self.assertNotEqual(code, 0)
        self.assertIn('与本入口 spec 不一致', error)
        code, output, error, _ = self.run_action({'job': 'not-a-task'}, scripted=None)
        self.assertNotEqual(code, 0)
        self.assertIn('32 位十六进制', error)


if __name__ == '__main__':
    unittest.main()
