"""Read-only export of existing platform groups for Komodo review/import.

Does not start containers or call Komodo. Output can contain environment values;
store it outside Git. Windows bind mounts require explicit Linux path adaptation.
"""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import tempfile


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def compose_inputs(root, group):
    registry = load(root / 'config/compose-groups.json')['groups']
    if group not in registry:
        raise ValueError('Unknown registered group')
    base = load(root / f'compose/groups/{group}.json')
    if set(base['services']) != set(registry[group]['services']):
        raise ValueError('Group services differ from registry')
    current = load(root / 'releases/current.compose.json')
    pins = current.get('services', {})
    return base, {'services': {s: copy.deepcopy(pins[s]) for s in base['services'] if s in pins}}


def export(root, output, groups, server):
    output.mkdir(parents=True, exist_ok=True)
    manifests = []
    rendered = []
    # Validate every group before writing any import file.
    for group in groups:
        base, override = compose_inputs(root, group)
        with tempfile.TemporaryDirectory(prefix='komodo-export-') as directory:
            overlay = Path(directory) / 'pins.json'
            overlay.write_text(json.dumps(override), encoding='utf-8')
            command = ['docker', 'compose', '--project-directory', str(root), '-p', group,
                       '-f', str(root / f'compose/groups/{group}.json'), '-f', str(overlay),
                       '--profile', '*', 'config', '--format', 'json']
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', check=True)
            effective = json.loads(result.stdout)
        for service in effective['services'].values():
            if any(v.get('type') == 'bind' for v in service.get('volumes', [])):
                raise ValueError(f'{group}: host bind mounts require reviewed Periphery path mapping')
        # Pin already-existing persistent volumes and networks as external so
        # importing never silently initializes a replacement with another name.
        for section in ('volumes', 'networks'):
            for name, settings in effective.get(section, {}).items():
                effective[section][name] = {'name': settings.get('name', f'{group}_{name}'), 'external': True}
        rendered.append((group, effective))
        manifests.append({'group': group, 'services': {s: c.get('image') for s, c in effective['services'].items()},
                          'overridden_services': sorted(override['services']), 'deployment_performed': False})
    blocks = []
    for group, effective in rendered:
        content = json.dumps(effective, ensure_ascii=False, indent=2)
        (output / f'{group}.compose.json').write_text(content + '\n', encoding='utf-8')
        blocks.append('\n'.join(['[[stack]]', 'name = ' + json.dumps(group), '[stack.config]',
                     'server = ' + json.dumps(server), 'project_name = ' + json.dumps(group),
                     'auto_update = false', 'poll_for_updates = false',
                     'file_contents = ' + json.dumps(content, ensure_ascii=False),
                     'extra_args = ["--no-build", "--pull", "never", "--wait", "--wait-timeout", "300"]']))
    (output / 'stacks.toml').write_text('\n\n'.join(blocks) + '\n', encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(manifests, indent=2), encoding='utf-8')
    return manifests


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--group', action='append', required=True)
    parser.add_argument('--server', default='Local')
    args = parser.parse_args()
    print(json.dumps(export(args.root.resolve(), args.output.resolve(), args.group, args.server), indent=2))
