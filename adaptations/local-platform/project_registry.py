"""Explicitly approved publishing capabilities; registration never invents a verifier."""
import json
from pathlib import Path
import re


def load_projects(path=None, *, data=None):
    if data is None:
        data = json.loads(Path(path or Path(__file__).with_name('projects.json')).read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('schema_version') != 1 or not isinstance(data.get('projects'),dict):
        raise ValueError('Invalid project registry')
    prefixes=set()
    for project, item in data['projects'].items():
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',project) or not isinstance(item,dict):
            raise ValueError('Invalid project identity')
        prefix=item.get('prefix')
        if not isinstance(prefix,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',prefix) or prefix in prefixes:
            raise ValueError('Invalid or duplicate resource prefix')
        prefixes.add(prefix)
        actions=item.get('actions')
        if not isinstance(actions,list) or not actions or any(a not in ('build-deploy','deploy','rollback') for a in actions) or len(actions)!=len(set(actions)):
            raise ValueError('Invalid actions')
        if not isinstance(item.get('title'),str) or not 0<len(item['title'])<=120:
            raise ValueError('Invalid title')
        if 'build-deploy' in actions:
            refs=item.get('refs')
            if not isinstance(refs,list) or not refs or any(not isinstance(ref,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}',ref) for ref in refs) or item.get('default_ref') not in refs:
                raise ValueError('Invalid approved build refs')
    return data['projects']


def render_action(project, config):
    source=Path(__file__).with_name('release-action.ts').read_text(encoding='utf-8')
    target={'type':'Procedure','id':config['prefix']+'-release'} if 'build-deploy' in config['actions'] else {'type':'Action','id':config['prefix']+'-deploy-version'}
    values={'__PROJECT__':project,'__TITLE__':config['title'],'__DEFAULT_REF__':config.get('default_ref'),
            '__NOTE__':config.get('note',''),'__METADATA_TARGET__':target}
    return re.sub('|'.join(map(re.escape,values)),
                  lambda match: json.dumps(values[match.group()],ensure_ascii=False),source)
