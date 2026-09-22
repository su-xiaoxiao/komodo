import unittest
from adopt_stacks import MARKER, inventory, config, retire


class RetireGuardTests(unittest.TestCase):
    """Komodo's DeleteStack tears down a running stack (Stack::pre_delete), so retire() must
    refuse unless the caller acknowledges that, and must never touch a foreign stack."""

    def test_retire_refuses_without_acknowledgement_and_makes_no_call(self):
        class API:
            def __init__(self):
                self.calls = []

            def call(self, path, body):
                self.calls.append(path)
                raise AssertionError('no API call may happen before the acknowledgement')

        api = API()
        with self.assertRaisesRegex(ValueError, 'destroys running stacks'):
            retire(api)
        self.assertEqual(api.calls, [])

    def test_retire_only_touches_owned_stacks(self):
        class API:
            def __init__(self):
                self.calls = []

            def call(self, path, body):
                self.calls.append((path, body))
                if path == 'read/ListStacks':
                    return [{'name': 'spec', 'id': '1'}, {'name': 'foreign', 'id': '2'}]
                if path == 'read/GetStack':
                    return {'description': MARKER + ' owned' if body['stack'] == '1' else 'someone else'}
                return {}

        api = API()
        result = retire(api, acknowledge_destroy=True)
        self.assertEqual(result['removed'], ['spec'])
        self.assertEqual(result['skipped_not_owned'], ['foreign'])
        self.assertEqual([body['id'] for path, body in api.calls if path == 'write/DeleteStack'], ['1'])

    def test_registration_blocks_native_start(self):
        settings = config('app', {'api': {'image': 'sha256:fixed'}}, 'server')
        self.assertEqual(settings['compose_cmd_wrapper_include'], ['up'])
        self.assertIn('Native stack start is disabled', settings['compose_cmd_wrapper'])


class AdoptionTests(unittest.TestCase):
    def item(self, group='app', service='api', oneoff='False'):
        return {'Image': 'sha256:fixed', 'Config': {'Env': ['SECRET=hidden'],
            'Labels': {'com.docker.compose.project': group,
                       'com.docker.compose.service': service,
                       'com.docker.compose.oneoff': oneoff}}}

    def test_only_registered_existing_services_without_secrets(self):
        actual = inventory([self.item(), self.item('temporary'), self.item(oneoff='True')],
                           {'app': {'services': ['api', 'not-created']}})
        self.assertEqual(actual, {'app': {'api': {'image': 'sha256:fixed'}}})

    def test_unknown_or_scaled_services_fail_closed(self):
        for items in ([self.item(service='unknown')], [self.item(), self.item()]):
            with self.assertRaises(ValueError):
                inventory(items, {'app': {'services': ['api']}})

    def test_snapshot_cannot_be_accidentally_deployed(self):
        settings = config('app', {'api': {'image': 'sha256:fixed'}}, 'server')
        self.assertFalse(settings['auto_pull'])
        self.assertFalse(settings['auto_update'])
        self.assertFalse(settings['webhook_enabled'])
        self.assertTrue(settings['pre_deploy']['shell_mode'])
        self.assertIn('exit 1', settings['pre_deploy']['command'])
