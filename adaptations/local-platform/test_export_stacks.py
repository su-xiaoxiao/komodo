import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('export_stacks', Path(__file__).with_name('export_stacks.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExportTests(unittest.TestCase):
    def test_current_pins_are_filtered_per_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, data in {
                'config/compose-groups.json': {'groups': {'demo': {'services': ['api']}}},
                'compose/groups/demo.json': {'services': {'api': {'image': 'old'}}},
                'releases/current.compose.json': {'services': {'api': {'image': 'new'}, 'other': {'image': 'unrelated'}}},
            }.items():
                target = root / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(data), encoding='utf-8')
            base, override = module.compose_inputs(root, 'demo')
            self.assertEqual(base['services']['api']['image'], 'old')
            self.assertEqual(override, {'services': {'api': {'image': 'new'}}})
            with self.assertRaises(ValueError):
                module.compose_inputs(root, '../other')


if __name__ == '__main__':
    unittest.main()
