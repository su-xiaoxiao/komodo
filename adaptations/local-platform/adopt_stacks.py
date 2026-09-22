"""Register existing Compose projects for lifecycle management, never deploy them.

The UI inventory is intentionally NOT a deployable Compose export. Actual images,
mounts and ports remain owned by the Windows release engine. No secrets imported.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from komodo_api import Komodo

MARKER = '[local-platform:lifecycle-v1]'
GUARD = 'echo "Use the verified Windows release pipeline to deploy this project" >&2; exit 1'


def inventory(containers, registry):
    groups = {}
    for item in containers:
        labels = item.get('Config', {}).get('Labels') or {}
        group = labels.get('com.docker.compose.project')
        service = labels.get('com.docker.compose.service')
        if group not in registry or labels.get('com.docker.compose.oneoff', '').lower() == 'true':
            continue
        if service not in registry[group]['services']:
            raise ValueError(f'{group}: unexpected service {service}')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', group):
            raise ValueError('Invalid Compose project')
        services = groups.setdefault(group, {})
        if service in services:
            raise ValueError(f'{group}: scaled services require explicit review')
        services[service] = {'image': item['Image']}
    return groups


def config(group, services, server):
    return dict(server_id=server, project_name=group,
                file_contents=json.dumps({'name': group, 'services': services}, indent=2),
                auto_pull=False, run_build=False, auto_update=False, poll_for_updates=False,
                webhook_enabled=False, destroy_before_deploy=False,
                pre_deploy={'command': GUARD, 'shell_mode': True},
                links=['http://127.0.0.1:28793/procedures'])


def install(api, groups, server):
    existing = {s['name']: s for s in api.call('read/ListStacks', {})}
    # Validate ownership of every target before mutating any resource.
    for group in groups:
        if group in existing:
            full = api.call('read/GetStack', {'stack': existing[group]['id']})
            if not full.get('description', '').startswith(MARKER):
                raise ValueError(f'{group}: existing Stack is not owned by this importer')
    result = []
    for group, services in sorted(groups.items()):
        settings = config(group, services, server)
        if group in existing:
            api.call('write/UpdateStack', {'id': existing[group]['id'], 'config': settings})
        else:
            api.call('write/CreateStack', {'name': group, 'config': settings})
        api.call('write/UpdateResourceMeta', {'target': {'type': 'Stack', 'id': group},
            'description': MARKER + '\n现有容器组：查看服务、日志及整组启停。发布期间请勿启停；版本更新走已验证发布流程。\n此处仅为服务清单，不含实际部署配置；共享数据库和跨组依赖需先启动。'})
        result.append({'group': group, 'services': sorted(services)})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('E:/jvjv/local-platform'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    registry = json.loads((args.root/'config/compose-groups.json').read_text(encoding='utf-8-sig'))['groups']
    ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True).split()
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids])) if ids else []
    groups = inventory(containers, registry)
    if args.apply:
        api = Komodo()
        server = next(s['id'] for s in api.call('read/ListServers', {}) if s['name'] == 'Local')
        print(json.dumps(install(api, groups, server), ensure_ascii=False, indent=2))
    else:
        print(json.dumps({k: list(v) for k, v in groups.items()}, indent=2))
