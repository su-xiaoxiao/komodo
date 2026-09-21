import json
from pathlib import Path
import tempfile
import time
import unittest
from bridge import Bridge


class Pipeline:
    def __init__(self):
        self.jobs = {}
        self.calls = 0

    def get_job(self, identity):
        if identity not in self.jobs:
            raise ValueError('Unknown job')
        return self.jobs[identity]

    def list_jobs(self):
        return list(self.jobs.values())

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
        self.home = Path(self.temp.name)
        self.pipeline = Pipeline()
        self.bridge = Bridge(self.home, self.pipeline)
        self.identity = 'a' * 32

    def request(self, **changes):
        body = dict(id=self.identity, project='mqtt-sandbox', action='build-deploy',
                    options={'ref': 'dev_necal'}, expires_at=time.time()+60)
        body.update(changes)
        path = self.home / 'requests' / (self.identity+'.json')
        path.write_text(json.dumps(body))
        return path

    def response(self):
        return json.loads((self.home/'responses'/ (self.identity+'.json')).read_text())

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

    def test_changed_request_identity_rejected(self):
        self.request()
        self.bridge.tick()
        self.request(action='rollback', options={})
        self.bridge.tick()
        self.assertEqual(self.pipeline.calls, 1)
        self.assertEqual(self.response()['status'], 'failed')

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
