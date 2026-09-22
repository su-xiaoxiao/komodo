"""Contract tests for the read-only Action templates' machine-readable line.

The project config page reads platform data by running these Actions and parsing one
`__PLATFORM_JSON__` line out of the Update logs, so the line is part of the page's contract:
it must be valid single-line JSON, declare its schema, and still be emitted when the action
fails (the page must be able to show a structured reason).

The templates are executed as rendered (Deno shim + Node), with a scripted queue response.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from project_registry import render_project_utility, render_template, render_action, load_projects

NODE = shutil.which('node')
MARKER = '__PLATFORM_JSON__'

SHIM = r'''
import { mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

class NotFound extends Error {}
Date.now = () => 1758000000000;   // fixed clock: the heartbeat below stays fresh, no real waiting
globalThis.setTimeout = (fn) => { fn(); return 0; };
globalThis.ARGS = JSON.parse(readFileSync(process.env.ARGS_JSON, 'utf8'));
const scripted = JSON.parse(process.env.SCRIPTED_RESPONSES || '[]');
const capture = { requests: [] };
process.on('exit', () => process.stdout.write('CAPTURE ' + JSON.stringify(capture) + '\n'));
let attempt = 0;

globalThis.Deno = {
  errors: { NotFound },
  readTextFile: (path) => {
    if (path.includes('/responses/') && scripted.length) {
      const body = scripted[Math.min(attempt++, scripted.length - 1)];
      if (body === null) throw new NotFound(path);
      return JSON.stringify(body);
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
class ReadActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.queue = self.home / 'queue'
        for name in ('requests', 'responses', 'claims'):
            (self.queue / name).mkdir(parents=True, exist_ok=True)
        (self.queue / 'heartbeat.json').write_text(json.dumps({'pid': 1, 'protocol': 1, 'updated_at': 1758000000}))

    def run_template(self, body, args=None, scripted=None, group='spec'):
        marker = 'const queue = "/local-queue";'
        self.assertIn(marker, body, 'the Action template no longer declares its queue path as expected')
        body = body.replace(marker, 'const queue = ' + json.dumps(str(self.queue).replace('\\', '/')) + ';')
        script = self.home / 'action.mjs'
        script.write_text(SHIM + body, encoding='utf-8')
        args_file = self.home / 'args.json'
        args_file.write_text(json.dumps(args or {}))
        environment = dict(os.environ, ARGS_JSON=str(args_file),
                           SCRIPTED_RESPONSES=json.dumps(scripted if scripted is not None else []))
        result = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', env=environment, timeout=120)
        lines, captured, payload = [], None, None
        for line in result.stdout.splitlines():
            if line.startswith('CAPTURE '):
                captured = json.loads(line[len('CAPTURE '):])
            elif line.startswith(MARKER):
                payload = json.loads(line[len(MARKER):].strip())
                lines.append(line)
            else:
                lines.append(line)
        return result.returncode, '\n'.join(lines), result.stderr, captured, payload

    def payload(self, returncode, output, error, captured, parsed):
        self.assertIsNotNone(parsed, 'no machine-readable line was printed: ' + output + error)
        return parsed

    def test_list_refs_line_is_valid_and_carries_the_source_authority(self):
        response = {'id': 'a' * 32, 'status': 'succeeded', 'kind': 'refs', 'project': 'mqtt-sandbox',
                    'mode': 'remote', 'remote': 'upstream', 'approved': ['dev_necal'], 'default_ref': 'dev_necal',
                    'approved_ref_tips': {'dev_necal': 'b' * 40}, 'branches': ['dev_necal', 'main'], 'branch_count': 2,
                    'tags': [], 'tag_count': 0, 'commits': [{'sha': 'b' * 40, 'subject': 't'}],
                    'requested_ref': None, 'requested_ref_approved': None, 'commit': None, 'resolution_error': None,
                    'released_commit': 'b' * 40}
        policy = load_projects()['mqtt-sandbox']
        body = render_action('mqtt-sandbox', policy, 'dev_necal', template='release-refs.ts',
                             target={'type': 'Action', 'id': 'mqtt-list-refs'})
        code, output, error, captured, parsed = self.run_template(body, scripted=[response])
        self.assertEqual(code, 0, output + error)
        self.assertEqual(parsed['schema'], 1)
        self.assertEqual(parsed['kind'], 'refs')
        self.assertEqual((parsed['mode'], parsed['remote']), ('remote', 'upstream'))
        self.assertEqual(parsed['approved'], ['dev_necal'])
        self.assertEqual(parsed['approved_ref_tips']['dev_necal'], 'b' * 40)
        self.assertEqual(captured['requests'][0]['action'], 'list-refs')
        self.assertIn('已登记分支', output, 'the human-readable view must survive')

    def test_list_refs_failure_still_prints_a_structured_reason(self):
        response = {'id': 'a' * 32, 'status': 'failed', 'kind': 'refs', 'project': 'mqtt-sandbox',
                    'error': 'ls-remote exited 128'}
        policy = load_projects()['mqtt-sandbox']
        body = render_action('mqtt-sandbox', policy, 'dev_necal', template='release-refs.ts',
                             target={'type': 'Action', 'id': 'mqtt-list-refs'})
        code, output, error, captured, parsed = self.run_template(body, scripted=[response])
        self.assertNotEqual(code, 0)
        self.assertEqual(parsed['status'], 'failed')
        self.assertEqual(parsed['error'], 'ls-remote exited 128')
        self.assertIn('ls-remote exited 128', error)

    def test_versions_line_carries_repository_recipe_release_and_running(self):
        response = {'id': 'c' * 32, 'status': 'succeeded', 'kind': 'versions', 'project': 'software-engineering',
                    'repository': {'head': 'd' * 40, 'branch': 'main', 'dirty': False, 'untracked': 1},
                    'recipe': {'digest': 'e' * 64, 'source_mode': 'local', 'approved_refs': ['main'], 'default_ref': 'main'},
                    'candidate': None, 'release': {'version': 'v1', 'commit': 'd' * 40, 'images': {'spec-api': 'sha256:' + 'f' * 64}},
                    'running': [{'service': 'spec-api', 'state': 'running', 'health': 'healthy',
                                 'image_id': 'sha256:' + 'f' * 64, 'matches_release': True, 'matches_candidate': True}],
                    'verdict': '运行镜像与最近成功发布一致'}
        body = render_template('release-versions.ts', {'__PROJECT__': 'software-engineering', '__TITLE__': '软件工程'})
        code, output, error, captured, parsed = self.run_template(body, scripted=[response])
        self.assertEqual(code, 0, output + error)
        self.assertEqual(parsed['recipe']['digest'], 'e' * 64)
        self.assertEqual(parsed['running'][0]['image_id'], 'sha256:' + 'f' * 64)
        self.assertEqual(parsed['verdict'], '运行镜像与最近成功发布一致')
        self.assertEqual(captured['requests'][0]['action'], 'versions')
        self.assertIn('版本信息', output)

    def test_source_edit_line_carries_the_recipe_digest_and_backup(self):
        response = {'id': 'a' * 32, 'status': 'succeeded', 'kind': 'source-edit', 'project': 'software-engineering',
                    'source': {'mode': 'local', 'refs': ['main'], 'default_ref': 'main'}, 'recipe_digest': 'a' * 64,
                    'backup': '.local/config-backups/x.json', 'unchanged_sections': ['build', 'release', 'verify']}
        policy = load_projects()['software-engineering']
        body = render_project_utility('software-engineering', policy, 'release-source.ts')
        code, output, error, captured, parsed = self.run_template(
            body, args={'mode': 'local', 'remote': '', 'refs': 'main', 'default_ref': 'main'}, scripted=[response])
        self.assertEqual(code, 0, output + error)
        self.assertEqual(parsed['recipe_digest'], 'a' * 64)
        self.assertEqual(parsed['source']['refs'], ['main'])
        self.assertEqual(captured['requests'][0]['action'], 'set-source')
        self.assertEqual(captured['requests'][0]['options']['mode'], 'local')

    def test_source_edit_failure_still_prints_the_reason(self):
        response = {'id': 'a' * 32, 'status': 'failed', 'kind': 'source-edit', 'project': 'software-engineering',
                    'error': 'source.refs must list at least one approved ref'}
        policy = load_projects()['software-engineering']
        body = render_project_utility('software-engineering', policy, 'release-source.ts')
        code, output, error, captured, parsed = self.run_template(
            body, args={'mode': 'local', 'remote': '', 'refs': 'main', 'default_ref': 'main'}, scripted=[response])
        self.assertNotEqual(code, 0)
        self.assertEqual(parsed['status'], 'failed')
        self.assertIn('at least one approved ref', error)

    def test_offline_executor_is_reported_without_touching_the_queue(self):
        (self.queue / 'heartbeat.json').write_text(json.dumps({'pid': 1, 'protocol': 1, 'updated_at': 1}))
        body = render_template('release-versions.ts', {'__PROJECT__': 'software-engineering', '__TITLE__': '软件工程'})
        code, output, error, captured, parsed = self.run_template(body, scripted=[])
        self.assertNotEqual(code, 0)
        self.assertIn('离线', error)
        self.assertIsNone(parsed)
        self.assertEqual(captured['requests'], [], 'an offline executor must not be sent a request')

    def test_groups_overview_line_carries_groups_containers_and_lock(self):
        response = {'id': 'e' * 32, 'status': 'succeeded', 'kind': 'groups', 'docker_error': None, 'generated_at': 'stamp',
                    'lock': {'held': True, 'kind': 'release', 'subject': 'software-engineering', 'pid': 4242},
                    'groups': [
                        {'name': 'spec', 'services': ['spec-api', 'spec-web'], 'running': 1, 'total': 1, 'expected': 2,
                         'missing': ['spec-web'], 'unexpected': [], 'managed': True, 'projects': [],
                         'containers': [{'name': 'spec-api', 'container_name': 'spec-spec-api-1', 'state': 'running',
                                         'health': 'healthy', 'image_id': 'sha256:' + 'a' * 64, 'drift': False}]},
                        {'name': 'other', 'services': ['other-api'], 'running': 1, 'total': 1, 'expected': 1,
                         'missing': [], 'unexpected': ['other-db'], 'managed': False, 'projects': [], 'containers': []}]}
        body = (Path(__file__).with_name('release-groups.ts')).read_text(encoding='utf-8')
        code, output, error, captured, parsed = self.run_template(body, scripted=[response])
        self.assertEqual(code, 0, output + error)
        self.assertEqual(parsed['kind'], 'groups')
        self.assertEqual(parsed['lock']['subject'], 'software-engineering')
        self.assertEqual([group['name'] for group in parsed['groups']], ['spec', 'other'])
        self.assertEqual(parsed['groups'][0]['missing'], ['spec-web'])
        self.assertEqual(captured['requests'][0]['action'], 'groups')
        self.assertEqual(captured['requests'][0]['project'], 'platform')
        self.assertIn('发布锁：被持有', output)
        self.assertIn('缺少 spec-web', output)
        self.assertIn('多出 other-db', output)

    def test_groups_overview_failure_still_prints_the_reason(self):
        response = {'id': 'e' * 32, 'status': 'failed', 'kind': 'groups', 'error': 'Docker 暂不可用'}
        body = (Path(__file__).with_name('release-groups.ts')).read_text(encoding='utf-8')
        code, output, error, captured, parsed = self.run_template(body, scripted=[response])
        self.assertNotEqual(code, 0)
        self.assertEqual(parsed['status'], 'failed')
        self.assertIn('Docker 暂不可用', error)


if __name__ == '__main__':
    unittest.main()
