"""Explicitly approved publishing capabilities; registration never invents a verifier.

This registry is the **approval** list: which project may run which action. It must
not hold a second copy of source, branch or command definitions - those live in the
platform authority `config/project-console.json` and are enforced by the pipeline,
so a branch or build command is defined in exactly one place. Unexpected keys are
rejected so the duplication cannot creep back in.
"""
import json
from pathlib import Path
import re

ALLOWED_KEYS = {'title', 'prefix', 'actions', 'note'}


def load_projects(path=None, *, data=None):
    if data is None:
        data = json.loads(Path(path or Path(__file__).with_name('projects.json')).read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('schema_version') != 1 or not isinstance(data.get('projects'),dict):
        raise ValueError('Invalid project registry')
    prefixes=set()
    for project, item in data['projects'].items():
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',project) or not isinstance(item,dict):
            raise ValueError('Invalid project identity')
        unexpected=sorted(set(item)-ALLOWED_KEYS)
        if unexpected:
            raise ValueError('Registry must not redefine source, refs or builds: '+', '.join(unexpected))
        prefix=item.get('prefix')
        if not isinstance(prefix,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',prefix) or prefix in prefixes:
            raise ValueError('Invalid or duplicate resource prefix')
        prefixes.add(prefix)
        actions=item.get('actions')
        if not isinstance(actions,list) or not actions or any(a not in ('build-deploy','deploy','rollback') for a in actions) or len(actions)!=len(set(actions)):
            raise ValueError('Invalid actions')
        if not isinstance(item.get('title'),str) or not 0<len(item['title'])<=120:
            raise ValueError('Invalid title')
        if 'note' in item and not isinstance(item['note'],str):
            raise ValueError('Invalid note')
    return data['projects']


def render_template(template, values):
    """Substitute the tokens a template declares; missing tokens are simply absent."""
    source=Path(__file__).with_name(template).read_text(encoding='utf-8')
    return re.sub('|'.join(map(re.escape,values)),
                  lambda match: json.dumps(values[match.group()],ensure_ascii=False),source)


def render_action(project, config, default_ref=None, template='release-action.ts', target=None):
    """Render an Action template. `default_ref` comes from the platform authority."""
    if target is None:
        target={'type':'Procedure','id':config['prefix']+'-release'} if 'build-deploy' in config['actions'] else {'type':'Action','id':config['prefix']+'-deploy-version'}
    values={'__PROJECT__':project,'__TITLE__':config['title'],'__DEFAULT_REF__':default_ref,
            '__NOTE__':config.get('note',''),'__METADATA_TARGET__':target}
    return render_template(template, values)


def render_stack_action(group, operation):
    """Render the lifecycle Action for one Compose group."""
    return render_template('release-stack.ts', {'__GROUP__':group,'__OPERATION__':operation})
