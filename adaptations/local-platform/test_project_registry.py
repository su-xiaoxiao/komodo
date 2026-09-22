import json
from pathlib import Path
import tempfile
import unittest
from bridge import validate
from komodo_api import default_refs, install
from project_registry import load_projects, render_action


class RegistryTests(unittest.TestCase):
    def test_mcp_allows_only_version_release_not_mqtt_build(self):
        projects = load_projects()
        body = dict(id='a'*32,project='mcp-gateway',action='deploy',options={'version':'v1'},expires_at=100)
        self.assertEqual(validate(body,body['id'],projects)['project'],'mcp-gateway')
        body.update(action='build-deploy',options={'ref':'dev_necal'})
        with self.assertRaises(ValueError): validate(body,body['id'],projects)

    def test_ref_shape_is_checked_here_and_membership_by_the_pipeline(self):
        projects = load_projects()
        for ref in ('bad;cmd', '--evil', '', 7, None):
            with self.subTest(ref=ref):
                body = dict(id='a'*32,project='mqtt-sandbox',action='build-deploy',options={'ref':ref},expires_at=100)
                with self.assertRaises(ValueError): validate(body,body['id'],projects)
        # A well formed ref is admitted here; the platform recipe owns the approved list.
        body = dict(id='a'*32,project='mqtt-sandbox',action='build-deploy',options={'ref':'feature/x'},expires_at=100)
        self.assertEqual(validate(body,body['id'],projects)['options'],{'ref':'feature/x'})

    def test_registry_refuses_a_second_copy_of_source_or_refs(self):
        for extra in ({'refs':['dev_necal']}, {'default_ref':'dev_necal'}, {'source_mode':'remote'},
                      {'build_commands':[['pwsh']]}, {'default_ref':'dev_necal','refs':['dev_necal']}):
            with self.subTest(extra=extra):
                item = {'title':'MQTT','prefix':'mqtt','actions':['build-deploy'],'note':'x'}
                item.update(extra)
                data = {'schema_version':1,'projects':{'mqtt-sandbox':item}}
                with self.assertRaisesRegex(ValueError, 'must not redefine'):
                    load_projects(data=data)

    def test_rendered_action_binds_project_and_metadata_target(self):
        text = render_action('mcp-gateway',load_projects()['mcp-gateway'])
        self.assertIn('const project = "mcp-gateway";',text)
        self.assertIn('"id": "mcp-deploy-version"',text)
        self.assertNotIn('ARGS.project',text)
        self.assertNotIn('__PROJECT__',text)

    def test_rendered_action_takes_default_ref_from_the_authority(self):
        policy = load_projects()['mqtt-sandbox']
        self.assertIn('const defaultRef = "dev_necal";', render_action('mqtt-sandbox', policy, 'dev_necal'))
        self.assertIn('const defaultRef = null;', render_action('mqtt-sandbox', policy))

    def test_duplicate_resource_prefix_is_rejected(self):
        data={'schema_version':1,'projects':{'one':{'title':'one','prefix':'same','actions':['deploy']},'two':{'title':'two','prefix':'same','actions':['deploy']}}}
        with self.assertRaises(ValueError): load_projects(data=data)

    def test_template_tokens_inside_values_are_preserved(self):
        policy=dict(load_projects()['mcp-gateway'],title='__NOTE__')
        self.assertIn('const title = "__NOTE__";',render_action('mcp-gateway',policy))

    def test_reinstall_keeps_selected_version_and_never_adds_mcp_build(self):
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

    def test_build_deploy_argument_default_comes_from_the_authority(self):
        class API:
            def __init__(self): self.calls=[]
            def call(self,path,body):
                self.calls.append((path,body))
                if path=='read/ListActions': return []
                if path=='read/ListProcedures': return []
                return {}
        api=API(); install(api, {'mqtt-sandbox':'dev_necal'})
        created=next(body for path,body in api.calls if path=='write/CreateAction' and body['name']=='mqtt-build-deploy')
        self.assertEqual(json.loads(created['config']['arguments'])['ref'],'dev_necal')
        self.assertIn('const defaultRef = "dev_necal";', created['config']['file_contents'])
        # Without an authority value the operator must type the ref; nothing is invented.
        api2=API(); install(api2, {})
        created=next(body for path,body in api2.calls if path=='write/CreateAction' and body['name']=='mqtt-build-deploy')
        self.assertEqual(json.loads(created['config']['arguments'])['ref'],'')

    def test_default_refs_reads_the_platform_recipe_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config').mkdir()
            (root/'config/project-console.json').write_text(json.dumps({'recipes':{
                'demo':{'source':{'mode':'local','default_ref':'main'}},
                'broken':{'source':{'mode':'nonsense'}}}}), encoding='utf-8')
            module = root/'services/project-console'
            module.mkdir(parents=True)
            (module/'recipe.py').write_text(
                'def normalize(recipe):\n'
                '    mode = recipe.get("source", {}).get("mode")\n'
                '    if mode not in ("local", "remote", "snapshot", "image-only"):\n'
                '        raise ValueError("Unknown source mode")\n'
                '    return {"source": recipe.get("source", {})}\n', encoding='utf-8')
            self.assertEqual(default_refs(root), {'demo':'main','broken':None})
            with self.assertRaisesRegex(ValueError, 'not found'):
                default_refs(root/'missing')

    def test_registry_rejects_unknown_project_shape(self):
        data={'schema_version':1,'projects':{'mqtt-sandbox':{'title':'MQTT','prefix':'mqtt','actions':['build-deploy'],'note':7}}}
        with self.assertRaisesRegex(ValueError, 'Invalid note'):
            load_projects(data=data)

    def test_list_refs_is_read_only_and_needs_no_separate_approval(self):
        projects = load_projects()
        body = dict(id='a'*32,project='mcp-gateway',action='list-refs',options={'ref':''},expires_at=100)
        self.assertEqual(validate(body,body['id'],projects)['action'],'list-refs')
        for ref in ('--help','bad;cmd','x'*257):
            with self.subTest(ref=ref):
                bad = dict(body,options={'ref':ref})
                with self.assertRaisesRegex(ValueError, 'Invalid ref'): validate(bad,bad['id'],projects)
        smuggled = dict(body,options={'ref':'','command':'whoami'})
        with self.assertRaisesRegex(ValueError, 'Unsupported action'): validate(smuggled,smuggled['id'],projects)

    def test_installer_adds_a_refs_action_only_where_build_is_approved(self):
        class API:
            def __init__(self): self.calls=[]
            def call(self,path,body):
                self.calls.append((path,body))
                return [] if path.startswith('read/') else {}
        api=API(); install(api, {'mqtt-sandbox':'dev_necal'})
        created={body['name']:body['config'] for path,body in api.calls if path=='write/CreateAction'}
        self.assertIn('mqtt-list-refs',created)
        self.assertNotIn('mcp-list-refs',created)
        refs_action=created['mqtt-list-refs']
        self.assertEqual(json.loads(refs_action['arguments']),{'ref':''})
        self.assertIn('const project = "mqtt-sandbox";',refs_action['file_contents'])
        self.assertIn('action: "list-refs"',refs_action['file_contents'])
        self.assertNotIn('__PROJECT__',refs_action['file_contents'])
        self.assertNotIn('部署',refs_action['file_contents'])
        self.assertNotIn('submit_once',refs_action['file_contents'])


if __name__ == '__main__':
    unittest.main()
