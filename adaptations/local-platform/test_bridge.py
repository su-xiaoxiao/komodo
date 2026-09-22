import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from bridge import Bridge


def bridge_stack_calls():
    return sys.modules['stack_groups'].CALLS

STACK_STUB = '''
import time
CALLS = []
BLOCK = {'event': None}

class StackError(RuntimeError):
    pass

class Stack:
    def __init__(self, root, run=None):
        self.root, self.run = root, run

    def execute(self, action, groups=(), services=()):
        CALLS.append((action, list(groups)))
        if BLOCK.get('burn'):
            # Stand-in for an operation that consumes the whole budget without a live docker call.
            time.sleep(BLOCK['burn'])
        if BLOCK.get('marker'):
            self.run(BLOCK['marker'])
        if BLOCK.get('hang'):
            self.run(BLOCK['hang'])
        if BLOCK['event'] is not None:
            BLOCK['event'].wait(10)
        if BLOCK.get('fail'):
            raise StackError('已有操作持有 releases/.release.lock（发布事务；项目 spec；pid 1）')
        return []
'''


class Pipeline:
    def __init__(self):
        self.jobs = {}
        self.calls = 0
        self.refs = 0
        self.refs_result = {'mode': 'remote', 'remote': 'upstream', 'approved': ['dev_necal'],
                            'default_ref': 'dev_necal', 'branches': ['dev_necal', 'main'],
                            'branch_count': 2, 'tags': [], 'tag_count': 0,
                            'requested_ref': None, 'requested_ref_approved': None,
                            'commit': None, 'resolution_error': None}
        self.refs_error = None

    def get_job(self, identity):
        if identity not in self.jobs:
            raise ValueError('Unknown job')
        return self.jobs[identity]

    def list_jobs(self):
        return list(self.jobs.values())

    def list_refs(self, project, ref=None):
        self.refs += 1
        if self.refs_error:
            raise self.refs_error
        result = dict(self.refs_result)
        if ref:
            result.update(requested_ref=ref, requested_ref_approved=ref in result['approved'],
                          commit='f' * 40 if ref in result['approved'] else None)
        return result

    def groups(self):
        self.groups_calls = getattr(self, 'groups_calls', 0) + 1
        if getattr(self, 'groups_error', None):
            raise self.groups_error
        return {'groups': [{'name': 'spec', 'services': ['spec-api'], 'containers': [
                    {'name': 'spec-api', 'state': 'running', 'health': 'healthy', 'image_id': 'sha256:' + 'a' * 64}],
                    'running': 1, 'total': 1, 'expected': 1, 'missing': [], 'unexpected': [], 'projects': [], 'managed': True}],
                'lock': {'held': False}, 'docker_error': None, 'generated_at': 'stamp'}

    def versions(self, project):
        self.versions_calls = getattr(self, 'versions_calls', 0) + 1
        if project != 'mqtt-sandbox':
            raise ValueError('Unknown project')
        return {'repository': {'head': 'a' * 40, 'branch': 'main', 'dirty': False, 'untracked': 1},
                'recipe': {'digest': 'b' * 64, 'source_mode': 'remote', 'approved_refs': ['dev_necal'], 'default_ref': 'dev_necal'},
                'candidate': None, 'release': {'version': 'v1'}, 'running': [], 'verdict': '运行镜像与最近成功发布一致'}

    def apply_source(self, project, payload):
        if not isinstance(payload, dict) or payload.get('mode') not in ('local', 'remote'):
            raise ValueError('Invalid source edit payload')
        return {'project': project, 'applied_at': 'stamp', 'source': {'mode': payload['mode'], 'refs': payload['refs'],
                                                                      'default_ref': payload['default_ref']},
                'previous_source': {'mode': 'remote'}, 'recipe_digest': 'c' * 64,
                'backup': '.local/config-backups/x.json', 'unchanged_sections': ['build', 'release', 'verify']}

    def submit_once(self, identity, project, action, **options):
        self.calls += 1
        job = dict(id=identity, project=project, action=action, options=options,
                   status='running', logs=[])
        self.jobs[identity] = job
        return job


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'queue'
        self.home.mkdir(parents=True)
        self.root = Path(self.temp.name) / 'platform'
        (self.root / 'scripts').mkdir(parents=True)
        (self.root / 'releases').mkdir(parents=True)
        (self.root / 'scripts/stack_groups.py').write_text(STACK_STUB, encoding='utf-8')
        sys.path.insert(0, str(self.root / 'scripts'))
        sys.modules.pop('stack_groups', None)
        (self.root / 'releases/current.compose.json').write_text(json.dumps({'services': {}, 'x-release-state': {}}), encoding='utf-8')
        self.pipeline = Pipeline()
        self.bridge = Bridge(self.home, self.pipeline, self.root)
        self.identity = 'a' * 32

    def request(self, **changes):
        identity = changes.pop('identity', self.identity)
        body = dict(id=identity, project='mqtt-sandbox', action='build-deploy',
                    options={'ref': 'dev_necal'}, expires_at=time.time()+60)
        body.update(changes)
        path = self.home / 'requests' / (identity+'.json')
        path.write_text(json.dumps(body))
        return path

    def response(self, identity=None):
        identity = identity or self.identity
        return json.loads((self.home/'responses'/ (identity+'.json')).read_text(encoding='utf-8-sig'))

    def test_repeat_request_reconciles_same_job(self):
        self.request()
        self.bridge.tick()
        self.bridge.tick()
        self.assertEqual(self.pipeline.calls, 1)
        self.assertEqual(self.response()['status'], 'running')
        self.pipeline.jobs[self.identity]['status'] = 'succeeded'
        self.bridge.tick()
        self.assertEqual(self.response()['status'], 'succeeded')

    def test_stop_uses_real_jobs_not_failed_observation(self):
        self.request()
        self.bridge.tick()
        (self.home/'responses'/ (self.identity+'.json')).write_text('{"status":"failed"}')
        self.assertTrue(self.bridge.active())
        self.pipeline.jobs[self.identity]['status'] = 'succeeded'
        self.assertFalse(self.bridge.active())

    def test_unknown_execution_state_defers_stop(self):
        from unittest.mock import patch
        with patch.object(self.pipeline, 'list_jobs', side_effect=PermissionError('busy')):
            self.assertTrue(self.bridge.active())

    def test_authoritative_terminal_report_is_frozen(self):
        self.request()
        self.bridge.tick()
        self.pipeline.jobs[self.identity]['status'] = 'succeeded'
        self.bridge.tick()
        first = self.response()
        self.assertTrue(first['terminal_verified'])
        self.bridge.tick()
        self.assertEqual(self.response(), first)

    def test_expired_request_never_executes(self):
        self.request(expires_at=time.time()-1)
        self.bridge.tick()
        self.assertEqual(self.pipeline.calls, 0)
        self.assertEqual(self.response()['status'], 'failed')

    def test_policy_revocation_does_not_invalidate_accepted_history(self):
        self.request()
        self.bridge.tick()
        self.bridge.projects = {}
        self.pipeline.jobs[self.identity]['status'] = 'succeeded'
        self.bridge.tick()
        self.assertEqual(self.response()['status'],'succeeded')
        self.assertEqual(self.pipeline.calls,1)

    def test_lost_response_recovers_terminal_job_without_execution(self):
        self.request()
        self.bridge.tick()
        self.pipeline.jobs[self.identity]['status'] = 'succeeded'
        (self.home/'responses'/ (self.identity+'.json')).unlink()
        self.bridge = Bridge(self.home, self.pipeline)
        self.bridge.tick()
        self.assertEqual(self.pipeline.calls, 1)
        self.assertEqual(self.response()['status'], 'succeeded')

    def test_transient_response_write_failure_does_not_fail_running_release(self):
        from unittest.mock import patch
        import bridge
        original = bridge.write
        def flaky(path, body):
            if path.parent.name == 'responses':
                raise PermissionError('Windows sharing violation')
            return original(path, body)
        self.request()
        with patch('bridge.write', side_effect=flaky):
            self.bridge.tick()
        self.bridge.tick()
        self.assertEqual(self.pipeline.calls, 1)
        self.assertEqual(self.response()['status'], 'running')

    def test_observation_failure_report_is_reconciled_against_real_job(self):
        self.request()
        self.bridge.tick()
        (self.home/'responses'/ (self.identity+'.json')).write_text(json.dumps({'status':'failed','error':'old transport error'}))
        self.pipeline.jobs[self.identity]['status'] = 'succeeded'
        self.bridge.tick()
        self.assertEqual(self.response()['status'], 'succeeded')
        self.assertEqual(self.pipeline.calls, 1)

    def test_claim_without_job_after_crash_is_not_retried(self):
        self.request()
        (self.home/'claims'/ (self.identity+'.json')).write_text('{}')
        self.bridge.tick()
        self.assertEqual(self.pipeline.calls, 0)
        self.assertEqual(self.response()['status'], 'interrupted')

    def test_pipeline_rejection_is_reported_and_kept(self):
        class Rejecting(Pipeline):
            def submit_once(self, identity, project, action, **options):
                raise ValueError('Unknown ref')

        bridge = Bridge(self.home, Rejecting())
        self.request()
        bridge.tick()
        first = json.loads((self.home/'responses'/ (self.identity+'.json')).read_text())
        self.assertEqual(first['status'], 'failed')
        self.assertIn('Unknown ref', first['error'])
        self.assertTrue((self.home/'claims'/ (self.identity+'.json')).exists())
        bridge.tick()
        again = json.loads((self.home/'responses'/ (self.identity+'.json')).read_text())
        self.assertEqual(again, first, 'the recorded admission error must not become interrupted')

    def test_list_refs_answers_without_claim_or_job(self):
        self.request(action='list-refs', options={'ref': ''})
        self.bridge.tick()
        result = self.response()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['kind'], 'refs')
        self.assertEqual(result['branches'], ['dev_necal', 'main'])
        self.assertEqual(self.pipeline.calls, 0)
        self.assertEqual(self.pipeline.refs, 1)
        self.assertFalse((self.home/'claims'/ (self.identity+'.json')).exists())
        self.bridge.tick()
        self.assertEqual(self.pipeline.refs, 1, 'a terminal ref answer must not be recomputed')
        self.assertEqual(self.response(), result)

    def test_list_refs_preview_reports_the_resolved_commit(self):
        self.request(action='list-refs', options={'ref': 'dev_necal'})
        self.bridge.tick()
        result = self.response()
        self.assertEqual(result['requested_ref'], 'dev_necal')
        self.assertTrue(result['requested_ref_approved'])
        self.assertEqual(result['commit'], 'f' * 40)
        self.assertEqual(self.pipeline.calls, 0)

    def test_list_refs_failure_is_reported_not_empty(self):
        self.pipeline.refs_error = RuntimeError('git exited 128; see logs')
        self.request(action='list-refs', options={'ref': ''})
        self.bridge.tick()
        result = self.response()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('git exited 128', result['error'])
        self.assertEqual(self.pipeline.calls, 0)
        self.assertFalse((self.home/'claims'/ (self.identity+'.json')).exists())

    def test_list_refs_rejects_invalid_ref_shape_without_asking_the_pipeline(self):
        for ref in ('--help', 'bad;cmd', 'x' * 257):
            with self.subTest(ref=ref):
                self.request(action='list-refs', options={'ref': ref})
                self.bridge.tick()
                self.assertEqual(self.response()['status'], 'failed')
                self.assertEqual(self.pipeline.refs, 0)
                (self.home/'responses'/ (self.identity+'.json')).unlink()

    def test_changed_request_identity_rejected(self):
        self.request()
        self.bridge.tick()
        self.request(action='rollback', options={})
        self.bridge.tick()
        self.assertEqual(self.pipeline.calls, 1)
        self.assertEqual(self.response()['status'], 'failed')

    def test_versions_action_answers_read_only(self):
        body = dict(id=self.identity, project='mqtt-sandbox', action='versions', options={}, expires_at=time.time() + 60)
        (self.home / 'requests' / (self.identity + '.json')).write_text(json.dumps(body))
        self.bridge.tick()
        result = self.response()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['kind'], 'versions')
        self.assertEqual(result['recipe']['approved_refs'], ['dev_necal'])
        self.assertEqual(result['verdict'], '运行镜像与最近成功发布一致')
        self.assertEqual(self.pipeline.calls, 0)
        self.assertFalse((self.home / 'claims' / (self.identity + '.json')).exists())
        self.bridge.tick()
        self.assertEqual(self.response(), result)

    def test_versions_action_reports_shape_failures(self):
        body = dict(id=self.identity, project='mqtt-sandbox', action='versions', options={'ref': 'x'}, expires_at=time.time() + 60)
        (self.home / 'requests' / (self.identity + '.json')).write_text(json.dumps(body))
        self.bridge.tick()
        self.assertEqual(self.response()['status'], 'failed')
        self.assertIn('Unsupported action', self.response()['error'])

    def test_source_edit_action_writes_through_the_pipeline(self):
        body = dict(id=self.identity, project='mqtt-sandbox', action='set-source',
                    options={'mode': 'local', 'remote': '', 'refs': ['main', 'dev_necal'], 'default_ref': 'main'},
                    expires_at=time.time() + 60)
        (self.home / 'requests' / (self.identity + '.json')).write_text(json.dumps(body))
        self.bridge.tick()
        result = self.response()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['kind'], 'source-edit')
        self.assertEqual(result['source']['refs'], ['main', 'dev_necal'])
        self.assertEqual(result['backup'], '.local/config-backups/x.json')
        self.assertEqual(result['unchanged_sections'], ['build', 'release', 'verify'])
        self.assertEqual(self.pipeline.calls, 0)

    def test_source_edit_action_validates_and_needs_a_source_project(self):
        for project, options, message in (
            ('mqtt-sandbox', {'mode': 'nonsense', 'remote': '', 'refs': ['main'], 'default_ref': 'main'}, 'Invalid source edit payload'),
            ('mqtt-sandbox', {'mode': 'local', 'remote': '', 'refs': 'main', 'default_ref': 'main'}, 'Invalid source edit payload'),
            ('mqtt-sandbox', {'mode': 'local', 'remote': '', 'refs': [], 'default_ref': ''}, 'Invalid source edit payload'),
            ('mcp-gateway', {'mode': 'local', 'remote': '', 'refs': ['main'], 'default_ref': 'main'}, 'no source release adapter'),
        ):
            with self.subTest(project=project, options=options):
                body = dict(id=self.identity, project=project, action='set-source', options=options, expires_at=time.time() + 60)
                (self.home / 'requests' / (self.identity + '.json')).write_text(json.dumps(body))
                self.bridge.tick()
                self.assertEqual(self.response()['status'], 'failed')
                self.assertIn(message, self.response()['error'])
                (self.home / 'responses' / (self.identity + '.json')).unlink()
        self.assertEqual(self.pipeline.calls, 0)

    def stack_request(self, **changes):
        body = dict(id=self.identity, project='spec', action='stack',
                    options={'operation': 'status', 'group': 'spec'}, expires_at=time.time() + 60)
        body.update(changes)
        path = self.home / 'requests' / (self.identity + '.json')
        path.write_text(json.dumps(body))
        return path

    def groups_request(self, **changes):
        body = dict(id=self.identity, project='platform', action='groups', options={}, expires_at=time.time() + 60)
        body.update(changes)
        path = self.home / 'requests' / (self.identity + '.json')
        path.write_text(json.dumps(body))
        return path

    def test_groups_action_is_read_only_and_carries_the_lock_state(self):
        self.groups_request()
        self.bridge.tick()
        result = self.response()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['kind'], 'groups')
        self.assertEqual(result['groups'][0]['name'], 'spec')
        self.assertEqual(result['groups'][0]['containers'][0]['state'], 'running')
        self.assertFalse(result['lock']['held'])
        self.assertEqual(self.pipeline.calls, 0, 'a read-only overview must never create a release job')
        self.assertFalse((self.home / 'claims' / (self.identity + '.json')).exists(), 'inline read: no claim')
        self.assertEqual(self.pipeline.groups_calls, 1)

    def test_groups_action_reports_failure_instead_of_an_empty_view(self):
        self.pipeline.groups_error = RuntimeError('docker unavailable')
        try:
            self.groups_request()
            self.bridge.tick()
            result = self.response()
            self.assertEqual(result['status'], 'failed')
            self.assertIn('docker unavailable', result['error'])
            self.assertNotIn('groups', result, 'a failure must not look like an empty group list')
        finally:
            self.pipeline.groups_error = None

    def test_groups_request_shape_is_validated(self):
        for changes in ({'options': {'group': 'spec'}}, {'action': 'groups', 'project': 'spec'}):
            with self.subTest(changes=changes):
                self.groups_request(**changes)
                self.bridge.tick()
                self.assertEqual(self.response()['status'], 'failed')
                (self.home / 'responses' / (self.identity + '.json')).unlink()

    def test_stack_operation_runs_on_the_platform_engine(self):
        self.stack_request()
        self.bridge.tick()
        deadline = time.time() + 5
        while time.time() < deadline and self.response()['status'] == 'running':
            time.sleep(0.05)
            self.bridge.tick()
        result = self.response()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['kind'], 'stack')
        self.assertEqual(result['stage'], 'done')
        stack = self.bridge.stack_runs[self.identity]
        self.assertIn(('Status', ['spec']), bridge_stack_calls())
        self.assertEqual(self.pipeline.calls, 0, 'a lifecycle request must never become a release job')
        claim = json.loads((self.home / 'claims' / (self.identity + '.json')).read_text(encoding='utf-8-sig'))
        self.assertEqual((claim['action'], claim['kind'], claim['project']), ('stack', 'stack', 'spec'))
        self.assertEqual(claim['options'], {'operation': 'status', 'group': 'spec'})

    def test_stack_rejection_is_reported_and_not_retried(self):
        import importlib
        stub = importlib.import_module('stack_groups')
        stub.BLOCK['fail'] = True
        try:
            self.stack_request()
            self.bridge.tick()
            deadline = time.time() + 5
            while time.time() < deadline and self.response()['status'] == 'running':
                time.sleep(0.05)
                self.bridge.tick()
            result = self.response()
            self.assertEqual(result['status'], 'failed')
            self.assertIn('release.lock', result['error'])
            self.bridge.tick()
            self.assertEqual(self.response(), result)
        finally:
            stub.BLOCK['fail'] = False

    def test_release_submission_is_refused_while_a_lifecycle_runs(self):
        import importlib, threading
        stub = importlib.import_module('stack_groups')
        stub.BLOCK['event'] = threading.Event()
        try:
            self.stack_request()
            self.bridge.tick()
            self.assertEqual(self.response()['status'], 'running')
            self.request(identity='b' * 32)  # build-deploy while the lifecycle holds the stage
            self.bridge.tick()
            self.assertEqual(self.pipeline.calls, 0)
            self.assertIn('lifecycle operation is in progress', self.response('b' * 32)['error'])
        finally:
            stub.BLOCK['event'].set()
            stub.BLOCK['event'] = None

    def test_release_submission_is_refused_while_the_shared_lock_is_held(self):
        (self.root / 'releases/.release.lock').write_text(json.dumps({'kind': 'stack', 'groups': ['spec'], 'pid': 4242}))
        self.request()
        self.bridge.tick()
        self.assertEqual(self.pipeline.calls, 0)
        error = self.response()['error']
        self.assertIn('release lock', error)
        self.assertIn('4242', error)

    def test_stack_requests_are_validated(self):
        for changes in ({'options': {'operation': 'restart', 'group': 'spec'}},
                        {'options': {'operation': 'status', 'group': 'Spec'}},
                        {'options': {'operation': 'status', 'group': 'spec', 'service': 'x'}},
                        {'project': 'mqtt-sandbox', 'options': {'operation': 'status', 'group': 'spec'}}):
            with self.subTest(changes=changes):
                self.stack_request(**changes)
                self.bridge.tick()
                self.assertEqual(self.response()['status'], 'failed')
                self.assertEqual(self.pipeline.calls, 0)
                self.assertEqual(self.bridge.stack_runs, {})
                (self.home / 'responses' / (self.identity + '.json')).unlink()

    def test_lifecycle_claim_is_durable_and_active_waits_for_it(self):
        import importlib, threading
        stub = importlib.import_module('stack_groups')
        stub.BLOCK['event'] = threading.Event()
        try:
            self.stack_request()
            self.bridge.tick()
            claim = json.loads((self.home / 'claims' / (self.identity + '.json')).read_text(encoding='utf-8-sig'))
            self.assertEqual(claim['action'], 'stack')
            self.assertEqual(claim['options'], {'operation': 'status', 'group': 'spec'})
            self.assertEqual(self.response()['status'], 'running')
            # A shutdown must wait for the in-flight operation instead of abandoning it.
            self.assertTrue(self.bridge.active())
        finally:
            stub.BLOCK['event'].set()
            stub.BLOCK['event'] = None
        deadline = time.time() + 5
        while time.time() < deadline and self.response()['status'] == 'running':
            time.sleep(0.05)
            self.bridge.tick()
        self.assertEqual(self.response()['status'], 'succeeded')
        self.assertFalse(self.bridge.active())

    def test_claim_of_a_dead_executor_is_interrupted_and_never_replayed(self):
        import importlib
        stub = importlib.import_module('stack_groups')
        before = len(stub.CALLS)
        self.stack_request()
        # An executor that claimed the operation and then died leaves a claim behind with
        # no durable execution record: the real container state is unknown.
        (self.home / 'claims' / (self.identity + '.json')).write_text(json.dumps(
            {'id': self.identity, 'project': 'spec', 'action': 'stack', 'kind': 'stack',
             'options': {'operation': 'start', 'group': 'spec'}, 'claimed_at': time.time()}))
        (self.home / 'responses' / (self.identity + '.json')).write_text(json.dumps({'id': self.identity, 'status': 'running'}))
        restarted = Bridge(self.home, self.pipeline, self.root)
        result = self.response()
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['stage'], 'claimed-without-executor')
        self.assertIn('unknown', result['error'])
        restarted.tick()
        self.assertEqual(self.response(), result, 'an interrupted lifecycle answer must stay the answer')
        self.assertEqual(len(stub.CALLS), before, 'a claimed lifecycle operation must never be replayed')

    def test_a_timed_out_lifecycle_is_reported_once_and_not_reverted(self):
        import importlib, threading
        stub = importlib.import_module('stack_groups')
        stub.BLOCK['event'] = threading.Event()
        self.bridge.stack_timeout = 0.2
        try:
            self.stack_request()
            self.bridge.tick()
            self.assertEqual(self.response()['status'], 'running')
            time.sleep(0.3)
            self.bridge.tick()
            result = self.response()
            self.assertEqual(result['status'], 'timed_out')
            self.assertEqual(result['stage'], 'timeout')
            self.assertIn('result is unknown', result['error'])
            self.assertEqual(result['job'], self.identity[:8])
            self.bridge.tick()
            self.assertEqual(self.response(), result, 'a timed out answer must not be reverted to running')
        finally:
            stub.BLOCK['event'].set()
            stub.BLOCK['event'] = None

    def test_a_timed_out_operation_stays_busy_until_the_background_ends(self):
        import importlib, threading
        stub = importlib.import_module('stack_groups')
        stub.BLOCK['event'] = threading.Event()
        self.bridge.stack_timeout = 0.2
        try:
            self.stack_request()
            self.bridge.tick()
            self.assertEqual(self.response()['status'], 'running')
            time.sleep(0.3)
            self.bridge.tick()
            reported = self.response()
            self.assertEqual(reported['status'], 'timed_out')
            # The verdict says 'unknown', but the background operation is still running: the
            # executor must not judge itself idle, and a release must still be refused.
            self.assertTrue(reported['execution']['running'])
            self.assertIn('still running', reported['error'])
            self.assertIn('reconcile', reported['execution']['note'])
            self.assertTrue(self.bridge.active())
            self.request(identity='b' * 32)
            self.bridge.tick()
            self.assertEqual(self.pipeline.calls, 0)
            self.assertIn('lifecycle operation is in progress', self.response('b' * 32)['error'])
        finally:
            stub.BLOCK['event'].set()
            stub.BLOCK['event'] = None
        deadline = time.time() + 5
        while time.time() < deadline and self.response()['execution']['running']:
            time.sleep(0.05)
            self.bridge.tick()
        settled = self.response()
        self.assertEqual(settled['status'], 'timed_out', 'the reported verdict is never rewritten')
        self.assertEqual(settled['stage'], 'timeout')
        self.assertEqual(settled['execution']['outcome'], 'succeeded', 'the real outcome is recorded separately')
        self.assertFalse(settled['execution']['running'])
        self.assertIn('reconcile', settled['execution']['note'])
        self.assertFalse(self.bridge.active())
        self.bridge.tick()
        self.assertEqual(self.response(), settled, 'once the outcome is recorded the answer stops changing')

    def test_no_further_docker_command_is_started_after_the_deadline(self):
        import importlib, tempfile
        from pathlib import Path
        stub = importlib.import_module('stack_groups')
        with tempfile.TemporaryDirectory() as sandbox:
            marker = Path(sandbox) / 'second-command-ran'
            stub.BLOCK['burn'] = 1.0                       # the operation spends its whole budget
            stub.BLOCK['marker'] = [sys.executable, '-c',
                                    'import pathlib; pathlib.Path(r"{0}").write_text("ran")'.format(marker)]
            self.bridge.stack_timeout = 0.2
            try:
                self.stack_request()
                self.bridge.tick()
                deadline = time.time() + 10
                while time.time() < deadline and self.response()['execution']['running']:
                    time.sleep(0.05)
                    self.bridge.tick()
                result = self.response()
                self.assertFalse(marker.exists(), 'a command must not be started after the overall deadline')
                self.assertTrue(result['execution']['deadline_guard'])
                self.assertIn('deadline', result['execution']['error'])
                self.assertIn('does not cancel', result['execution']['error'])
                self.assertEqual(self.response()['status'], 'timed_out', 'the operator still gets the unknown verdict')
            finally:
                stub.BLOCK['burn'] = None
                stub.BLOCK['marker'] = None

    def test_restart_clears_a_stale_still_executing_record(self):
        import importlib
        stub = importlib.import_module('stack_groups')
        before = len(stub.CALLS)
        self.stack_request()
        (self.home / 'claims' / (self.identity + '.json')).write_text(json.dumps(
            {'id': self.identity, 'project': 'spec', 'action': 'stack', 'kind': 'stack',
             'options': {'operation': 'start', 'group': 'spec'}, 'claimed_at': time.time()}))
        reported = {'id': self.identity, 'kind': 'stack', 'project': 'spec', 'group': 'spec', 'operation': 'start',
                    'status': 'timed_out', 'stage': 'timeout', 'job': self.identity[:8], 'output': [],
                    'error': 'Lifecycle operation exceeded 900s', 'observed_at': time.time(),
                    'execution': {'running': True, 'outcome': None, 'finished_at': None, 'deadline_guard': False,
                                  'note': 'verdict reported while the background operation was still running'}}
        (self.home / 'responses' / (self.identity + '.json')).write_text(json.dumps(reported))
        restarted = Bridge(self.home, self.pipeline, self.root)
        result = self.response()
        # The verdict stands, the stale 'still executing' claim does not.
        self.assertEqual(result['status'], 'timed_out')
        self.assertEqual(result['stage'], 'timeout')
        self.assertEqual(result['error'], reported['error'])
        self.assertFalse(result['execution']['running'])
        self.assertTrue(result['execution']['restarted'])
        self.assertIn('reconcile', result['execution']['note'])
        self.assertFalse(restarted.active())
        restarted.tick()
        self.assertEqual(self.response(), result, 'a reconciled answer must not keep changing')
        self.assertEqual(len(stub.CALLS), before, 'reconciliation must never execute anything')

    def test_a_hung_docker_call_fails_instead_of_reporting_progress_forever(self):
        import importlib
        stub = importlib.import_module('stack_groups')
        stub.BLOCK['hang'] = [sys.executable, '-c', 'import time; time.sleep(30)']
        self.bridge.command_timeout = 0.4
        try:
            self.stack_request()
            self.bridge.tick()
            deadline = time.time() + 15
            while time.time() < deadline and self.response()['status'] == 'running':
                time.sleep(0.05)
                self.bridge.tick()
            result = self.response()
            self.assertEqual(result['status'], 'failed')
            self.assertIn('timed out', result['error'])
        finally:
            stub.BLOCK['hang'] = None

    def test_other_projects_and_command_injection_rejected(self):
        for changes in ({'project':'docforge'}, {'options':{'command':'whoami'}},
                        {'id':'../oops'}, {'action':'shell'}, {'options':{'ref':'bad;cmd'}}):
            with self.subTest(changes=changes):
                self.request(**changes)
                self.bridge.tick()
                self.assertEqual(self.pipeline.calls, 0)
                self.assertEqual(self.response()['status'], 'failed')
                (self.home/'responses'/ (self.identity+'.json')).unlink()


if __name__ == '__main__':
    unittest.main()
