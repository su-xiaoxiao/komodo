import unittest
from adopt_stacks import inventory, config


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
