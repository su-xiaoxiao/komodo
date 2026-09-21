import unittest
from bridge import validate
from project_registry import load_projects, render_action


class RegistryTests(unittest.TestCase):
    def test_mcp_allows_only_version_release_not_mqtt_build(self):
        projects = load_projects()
        body = dict(id='a'*32,project='mcp-gateway',action='deploy',options={'version':'v1'},expires_at=100)
        self.assertEqual(validate(body,body['id'],projects)['project'],'mcp-gateway')
        body.update(action='build-deploy',options={'ref':'dev_necal'})
        with self.assertRaises(ValueError): validate(body,body['id'],projects)

    def test_mqtt_ref_stays_restricted(self):
        body = dict(id='a'*32,project='mqtt-sandbox',action='build-deploy',options={'ref':'other'},expires_at=100)
        with self.assertRaises(ValueError): validate(body,body['id'],load_projects())

    def test_rendered_action_binds_project_and_metadata_target(self):
        text = render_action('mcp-gateway',load_projects()['mcp-gateway'])
        self.assertIn('const project = "mcp-gateway";',text)
        self.assertIn('"id": "mcp-deploy-version"',text)
        self.assertNotIn('ARGS.project',text)
        self.assertNotIn('__PROJECT__',text)

    def test_duplicate_resource_prefix_is_rejected(self):
        data={'schema_version':1,'projects':{'one':{'title':'one','prefix':'same','actions':['deploy']},'two':{'title':'two','prefix':'same','actions':['deploy']}}}
        with self.assertRaises(ValueError): load_projects(data=data)

    def test_template_tokens_inside_values_are_preserved(self):
        policy=dict(load_projects()['mcp-gateway'],title='__NOTE__')
        self.assertIn('const title = "__NOTE__";',render_action('mcp-gateway',policy))

    def test_reinstall_keeps_selected_version_and_never_adds_mcp_build(self):
        from komodo_api import install
        class API:
            def __init__(self): self.calls=[]
            def call(self,path,body):
                self.calls.append((path,body))
                if path=='read/ListActions': return [{'name':'mcp-deploy-version','id':'existing'}]
                if path=='read/ListProcedures': return []
                return {}
        api=API(); install(api)
        update=next(body for path,body in api.calls if path=='write/UpdateAction' and body['id']=='existing')
        self.assertNotIn('arguments',update['config'])
        self.assertFalse(any(body.get('name')=='mcp-build-deploy' for _,body in api.calls))
