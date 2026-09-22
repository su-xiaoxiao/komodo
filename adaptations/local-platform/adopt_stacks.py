"""Register existing Compose projects for lifecycle management, never deploy them.

The UI inventory is intentionally NOT a deployable Compose export. Actual images,
mounts and ports remain owned by the Windows release engine. No secrets imported.

2026-09-22 step 2: the daily group lifecycle moved to the Komodo page `/platform`
(package `ui/src/pages/platform`), whose controls drive the installed `<group>-stack` Actions and
therefore take releases/.release.lock. Komodo's native Start/Stop buttons run `docker compose up`
/ `docker compose stop` straight on the daemon and bypass that lock, so the registrations are
retired (`--retire`) and only kept as an inventory when someone deliberately re-adopts them.
Re-adopting also installs a `compose_cmd_wrapper` that refuses the native `up` (defence in depth);
native `stop` has no supported hook and is the reason the resources stay retired.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from komodo_api import Komodo

MARKER = '[local-platform:lifecycle-v1]'
GUARD = 'echo "Use the verified Windows release pipeline to deploy this project" >&2; exit 1'
# Refuses a native `docker compose up` for these groups and points at the locked entry.
WRAPPER = 'sh -c \'echo "Native stack start is disabled: use the platform page / <group>-stack entry, which shares releases/.release.lock" >&2; exit 1\' # [[COMPOSE_COMMAND]]'


def owned(existing):
    """Stacks this importer created (never touch a stack someone else registered)."""
    return {name: item for name, item in existing.items() if str(item.get('description') or '').startswith(MARKER)}


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
            'description': MARKER + '\n现有容器组：查看服务与日志。**启停请用 Komodo 页面「本机平台」或各组 <组名>-stack 入口**（与发布事务共用 releases/.release.lock）；原生 Start/Stop 按钮不经过该锁，已在 2026-09-22 第 2 步退役登记。版本更新走已验证发布流程。'})
        result.append({'group': group, 'services': sorted(services)})
    return result


def retire(api, names=None, acknowledge_destroy=False):
    """Delete the stacks this importer owns.

    DANGER, 2026-09-22 incident: Komodo's `DeleteStack` is NOT a record-only unregister. Its
    `Stack::pre_delete` (bin/core/src/resource/stack.rs) reads "If it is Up, it should be taken
    down ... stack needs to be destroyed" and tears the running group down on the host. Calling it
    on the eight registered groups destroyed mysql/docforge/fmea/spec/mcp/project-console; they had
    to be restored with `scripts/Stack.ps1 -Action Start -Group <group>`. The daily entry is the
    platform page, so retirement is not needed - but if it is ever really wanted, it must be
    acknowledged explicitly and run only while the groups are already stopped.
    """
    if not acknowledge_destroy:
        raise ValueError('retire() destroys running stacks (Komodo Stack::pre_delete); '
                         'pass acknowledge_destroy=True only after stopping the groups yourself')
    existing = {s['name']: s for s in api.call('read/ListStacks', {})}
    full = {}
    for name, item in existing.items():
        detail = api.call('read/GetStack', {'stack': item['id']})
        if str(detail.get('description') or '').startswith(MARKER):
            full[name] = item['id']
    removed, skipped = [], sorted(set(existing) - set(full))
    for name in sorted(full):
        if names and name not in names:
            continue
        api.call('write/DeleteStack', {'id': full[name]})
        removed.append(name)
    return {'removed': removed, 'skipped_not_owned': skipped}


def config(group, services, server):
    return dict(server_id=server, project_name=group,
                file_contents=json.dumps({'name': group, 'services': services}, indent=2),
                auto_pull=False, run_build=False, auto_update=False, poll_for_updates=False,
                webhook_enabled=False, destroy_before_deploy=False,
                compose_cmd_wrapper=WRAPPER, compose_cmd_wrapper_include=['up'],
                pre_deploy={'command': GUARD, 'shell_mode': True},
                links=['http://127.0.0.1:28793/platform'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('E:/jvjv/local-platform'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--retire', action='store_true',
                        help='delete the stacks this importer owns; DESTROYS running groups (see retire())')
    parser.add_argument('--acknowledge-destroy', action='store_true',
                        help='required together with --retire: confirms the groups are already stopped')
    parser.add_argument('--group', action='append', default=None, help='limit --retire to these groups')
    args = parser.parse_args()
    api = Komodo()
    if args.retire:
        print(json.dumps(retire(api, set(args.group) if args.group else None,
                                acknowledge_destroy=args.acknowledge_destroy), ensure_ascii=False, indent=2))
        raise SystemExit(0)
    registry = json.loads((args.root/'config/compose-groups.json').read_text(encoding='utf-8-sig'))['groups']
    ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True).split()
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids])) if ids else []
    groups = inventory(containers, registry)
    if args.apply:
        server = next(s['id'] for s in api.call('read/ListServers', {}) if s['name'] == 'Local')
        print(json.dumps(install(api, groups, server), ensure_ascii=False, indent=2))
    else:
        print(json.dumps({k: list(v) for k, v in groups.items()}, indent=2))
