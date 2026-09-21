"""Local-only Komodo API client; credentials stay in the ignored environment file."""
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


def install(api):
    from project_registry import load_projects, render_action
    actions = {item['name']: item for item in api.call('read/ListActions', {})}
    procedures = {item['name']: item for item in api.call('read/ListProcedures', {})}
    for project, policy in load_projects().items():
        prefix = policy['prefix']
        source = render_action(project, policy)
        definitions = [('reconcile', {'operation':'reconcile','job':''})]
        if 'build-deploy' in policy['actions']:
            definitions.append(('build-deploy', {'operation':'build-deploy','ref':policy['default_ref']}))
        if 'deploy' in policy['actions']:
            definitions.append(('deploy-version', {'operation':'deploy','version':''}))
        if 'rollback' in policy['actions']:
            definitions.append(('rollback', {'operation':'rollback'}))
        for suffix, arguments in definitions:
            name = prefix + '-' + suffix
            config = dict(file_contents=source, arguments=json.dumps(arguments), arguments_format='json',
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
    install(Komodo())
