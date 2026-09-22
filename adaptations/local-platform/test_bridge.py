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
        body = dict(id=self.identity, project='mqtt-sandbox', action='build-deploy',
                    options={'ref': 'dev_necal'}, expires_at=time.time()+60)
        body.update(changes)
        path = self.home / 'requests' / (self.identity+'.json')
        path.write_text(json.dumps(body))
        return path

    def response(self):
        return json.loads((self.home/'responses'/ (self.identity+'.json')).read_text(encoding='utf-8-sig'))

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

    def stack_request(self, **changes):
        body = dict(id=self.identity, project='spec', action='stack',
                    options={'operation': 'status', 'group': 'spec'}, expires_at=time.time() + 60)
        body.update(changes)
        path = self.home / 'requests' / (self.identity + '.json')
        path.write_text(json.dumps(body))
        return path

    def test_changed_request_identity_rejected(self):
        self.request()
        self.bridge.tick()
        self.request(action='rollback', options={})
        self.bridge.tick()
        self.assertEqual(self.pipeline.calls, 1)
        self.assertEqual(self.response()['status'], 'failed')

    def stack_request(self, **changes):
        body = dict(id=self.identity, project='spec', action='stack',
                    options={'operation': 'status', 'group': 'spec'}, expires_at=time.time() + 60)
        body.update(changes)
        path = self.home / 'requests' / (self.identity + '.json')
        path.write_text(json.dumps(body))
        return path

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
        self.assertFalse((self.home / 'claims' / (self.identity + '.json')).exists())

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
            self.request()  # build-deploy for mqtt-sandbox while the lifecycle holds the stage
            self.bridge.tick()
            self.assertEqual(self.pipeline.calls, 0)
            self.assertIn('lifecycle operation is in progress', self.response()['error'])
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
