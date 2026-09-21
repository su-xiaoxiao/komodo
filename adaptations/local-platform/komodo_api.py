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


if __name__ == '__main__':
    api = Komodo()
    source = Path(__file__).with_name('mqtt-action.ts').read_text(encoding='utf-8')
    actions = {item['name']: item for item in api.call('read/ListActions', {})}
    for name, arguments in [
        ('mqtt-build-deploy', {'operation':'build-deploy','ref':'dev_necal'}),
        ('mqtt-deploy-version', {'operation':'deploy','version':''}),
        ('mqtt-rollback', {'operation':'rollback'}),
        ('mqtt-reconcile', {'operation':'reconcile','job':''}),
    ]:
        config = dict(file_contents=source, arguments=json.dumps(arguments), arguments_format='json',
                      run_at_startup=False, schedule_enabled=False, webhook_enabled=False)
        if name in actions:
            result = api.call('write/UpdateAction', {'id':actions[name]['id'],'config':config})
        else:
            result = api.call('write/CreateAction', {'name':name,'config':config})
        print(name, result.get('_id', result.get('id')))
    procedures = {item['name']: item for item in api.call('read/ListProcedures', {})}
    for name, action, stage in [
        ('mqtt-release', 'mqtt-build-deploy', '构建 → 隔离验收 → 发布 → 健康检查'),
        ('mqtt-restore', 'mqtt-rollback', '回退上一已验证版本'),
    ]:
        config = dict(schedule_enabled=False, webhook_enabled=False, stages=[{
            'name': stage, 'enabled': True, 'executions': [{'enabled':True,
            'execution':{'type':'RunAction','params':{'action':action}}}]}])
        if name in procedures:
            result = api.call('write/UpdateProcedure', {'id':procedures[name]['id'],'config':config})
        else:
            result = api.call('write/CreateProcedure', {'name':name,'config':config})
        print(name, result.get('_id', result.get('id')))
