"""Local-only Komodo API client; credentials stay in the ignored environment file.

The installer derives the UI default branch from the platform authority
(`config/project-console.json`) instead of a second copy inside this repository.
"""
import argparse
import importlib.util
import json
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Komodo redirect rejected')


class Komodo:
    def __init__(self, root=None):
        root = Path(root) if root else Path(__file__).resolve().parents[2]
        settings = dict(line.split('=', 1) for line in (root/'.local/komodo.env').read_text().splitlines() if '=' in line)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.token = None
        self.token = self.call('auth/login/LoginLocalUser', dict(username='admin', password=settings['KOMODO_LOCAL_PASSWORD']))['data']['jwt']

    def call(self, path, body, timeout=30):
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = self.token
        request = Request('http://127.0.0.1:28793/'+path, json.dumps(body).encode(), headers)
        with self.opener.open(request, timeout=timeout) as response:
            return json.load(response)


def default_refs(platform_root):
    """Read the authoritative recipes so a branch default has exactly one source.

    Loads the platform's recipe module by path: this repository must not grow a
    second parser that could drift from the one the pipeline actually uses.
    """
    root = Path(platform_root)
    module_path = root / 'services/project-console/recipe.py'
    config_path = root / 'config/project-console.json'
    if not module_path.is_file() or not config_path.is_file():
        raise ValueError('Platform recipe module or config not found under ' + str(root))
    spec = importlib.util.spec_from_file_location('platform_recipe', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    refs = {}
    for project, recipe in config.get('recipes', {}).items():
        try:
            refs[project] = module.normalize(recipe)['source'].get('default_ref')
        except ValueError:
            refs[project] = None
    return refs


def install(api, refs=None):
    from project_registry import load_projects, render_action
    refs = refs or {}
    actions = {item['name']: item for item in api.call('read/ListActions', {})}
    procedures = {item['name']: item for item in api.call('read/ListProcedures', {})}
    for project, policy in load_projects().items():
        prefix = policy['prefix']
        source = render_action(project, policy, refs.get(project))
        definitions = [('reconcile', {'operation':'reconcile','job':''})]
        if 'build-deploy' in policy['actions']:
            definitions.append(('build-deploy', {'operation':'build-deploy','ref':refs.get(project) or ''}))
            # Read-only branch/tag listing and pre-build preview for the same project.
            definitions.append(('list-refs', {'ref':''}))
        if 'deploy' in policy['actions']:
            definitions.append(('deploy-version', {'operation':'deploy','version':''}))
        if 'rollback' in policy['actions']:
            definitions.append(('rollback', {'operation':'rollback'}))
        for suffix, arguments in definitions:
            name = prefix + '-' + suffix
            if suffix == 'list-refs':
                body = render_action(project, policy, None, template='release-refs.ts',
                                     target={'type':'Action','id':name})
            else:
                body = source
            config = dict(file_contents=body, arguments=json.dumps(arguments), arguments_format='json',
                          run_at_startup=False, schedule_enabled=False, webhook_enabled=False)
            if name in actions:
                # Keep the operator's explicit version/ref selection across adapter upgrades.
                config.pop('arguments')
                config.pop('arguments_format')
                result = api.call('write/UpdateAction', {'id':actions[name]['id'],'config':config})
            else:
                result = api.call('write/CreateAction', {'name':name,'config':config})
                api.call('write/UpdateResourceMeta', {'target':{'type':'Action','id':name},'description':policy.get('note','')})
            print(name, result.get('_id', result.get('id')))
        definitions = []
        if 'build-deploy' in policy['actions']:
            definitions.append(('release','build-deploy','构建 → 项目专用验收 → 发布 → 健康检查'))
        if 'rollback' in policy['actions']:
            definitions.append(('restore','rollback','回退上一已验证版本'))
        for suffix, action, stage in definitions:
            name = prefix + '-' + suffix
            config = dict(schedule_enabled=False, webhook_enabled=False, stages=[{
                'name':stage,'enabled':True,'executions':[{'enabled':True,
                'execution':{'type':'RunAction','params':{'action':prefix+'-'+action}}}]}])
            if name in procedures:
                result = api.call('write/UpdateProcedure', {'id':procedures[name]['id'],'config':config})
            else:
                result = api.call('write/CreateProcedure', {'name':name,'config':config})
            print(name, result.get('_id', result.get('id')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform-root', type=Path, default=Path('E:/jvjv/local-platform'),
                        help='Platform checkout holding config/project-console.json')
    options = parser.parse_args()
    install(Komodo(), default_refs(options.platform_root))
