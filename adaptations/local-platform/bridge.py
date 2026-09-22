"""Windows worker for approved Komodo releases. No listening network port."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
from project_registry import load_projects

TERMINAL = {'succeeded', 'failed', 'interrupted'}
# A lifecycle operation that runs past its deadline is reported as timed out; the
# operator must re-check with the same entry instead of assuming it never started.
STACK_TERMINAL = TERMINAL | {'timed_out'}
STACK_CLAIM_REASON = ('Executor restarted or lost the operation state; the lifecycle result is unknown. '
                      'Re-check with the same -stack entry using operation=status and only resubmit if needed.')


def read(path):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Queue file too large')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, body):
    pending = path.with_suffix('.tmp-' + uuid.uuid4().hex)
    with pending.open('w', encoding='utf-8') as stream:
        json.dump(body, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        # Docker Desktop may briefly hold a Windows file without delete-sharing.
        for attempt in range(40):
            try:
                os.replace(pending, path)
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(.05)
    finally:
        pending.unlink(missing_ok=True)


def validate(body, identity, projects=None, *, admission=True):
    projects = load_projects() if projects is None else projects
    if (not isinstance(body, dict) or set(body) != {'id','project','action','options','expires_at'}
            or body['id'] != identity or not isinstance(body['project'], str)
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',body['project'])):
        raise ValueError('Invalid request identity or project')
    if admission and body['project'] not in projects and body.get('action') != 'stack':
        # Lifecycle requests name a Compose group, not a registered project; which groups
        # exist is decided by the platform group registry, not by this approval list.
        raise ValueError('Unregistered project')
    allowed = {'build-deploy': {'ref'}, 'deploy': {'version'}, 'rollback': set(), 'list-refs': {'ref'},
               'stack': {'operation', 'group'}, 'versions': set(),
               'set-source': {'mode', 'remote', 'refs', 'default_ref'}}
    action, options = body['action'], body['options']
    policy = projects.get(body['project'], {})
    if not isinstance(action, str) or action not in allowed or not isinstance(options, dict) or set(options) != allowed[action]:
        raise ValueError('Unsupported action or options')
    if admission and action in ('set-source', 'versions') and 'build-deploy' not in policy.get('actions', []):
        raise ValueError('This project has no source release adapter')
    if admission and action not in ('list-refs', 'stack', 'versions', 'set-source') and action not in policy['actions']:
        # list-refs only reveals refs of a source the recipe already declares, so it
        # needs no separate approval; every action that executes does.
        raise ValueError('This project has no approved adapter for the requested action')
    if action == 'set-source':
        # Reviewed write to the authoritative recipe: shape only, the platform module
        # validates the block with the same normalizer the pipeline uses.
        if (options['mode'] not in ('local', 'remote') or not isinstance(options['remote'], str)
                or not isinstance(options['default_ref'], str) or not isinstance(options['refs'], list)
                or not 0 < len(options['refs']) <= 20
                or any(not isinstance(item, str) or not item for item in options['refs'])):
            raise ValueError('Invalid source edit payload')
    if action == 'stack':
        # Lifecycle requests carry the *group* in the project field; the platform's
        # compose-groups.json is the authority for which groups exist, and the shared
        # release lock is what keeps start/stop exclusive with a release transaction.
        if (options['operation'] not in ('start', 'stop', 'status', 'logs')
                or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', options['group'])
                or body['project'] != options['group']):
            raise ValueError('Invalid stack operation or group')
    if action == 'list-refs' and (not isinstance(options['ref'], str) or len(options['ref']) > 256
            or (options['ref'] and (options['ref'].startswith('-') or not re.fullmatch(r'[A-Za-z0-9_./-]+', options['ref'])))):
        raise ValueError('Invalid ref')
    if action == 'build-deploy' and (not isinstance(options['ref'],str)
            or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}',options['ref'])):
        # Which branches may be built is defined once, in the platform recipe that
        # the pipeline validates; this registry only approves the action itself.
        raise ValueError('Invalid ref')
    if action == 'deploy' and (not isinstance(options['version'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}', options['version'])):
        raise ValueError('Invalid version')
    if type(body['expires_at']) not in (int, float) or not 0 < body['expires_at'] < 1e12:
        raise ValueError('Invalid expiry')
    return {key: body[key] for key in ('id', 'project', 'action', 'options')}


class Bridge:
    def __init__(self, home, pipeline, root=None):
        self.home, self.pipeline = Path(home), pipeline
        self.root = Path(root) if root else None
        self.projects = load_projects()
        self.stack_runs = {}
        # Every lifecycle operation is bounded: a hung docker call must not leave a run
        # reported as running forever while the Komodo Action waits for an answer.
        self.stack_timeout = float(os.environ.get('LOCAL_STACK_TIMEOUT', '900'))
        # A single docker call gets slightly less than the whole budget, so a hung call is
        # reported as a failed command instead of an unknown overall result.
        self.command_timeout = float(os.environ.get('LOCAL_STACK_COMMAND_TIMEOUT', str(self.stack_timeout * 0.8)))
        for name in ('requests', 'responses', 'claims'):
            (self.home/name).mkdir(parents=True, exist_ok=True)
        # A claimed lifecycle operation whose executor died has no durable execution record,
        # so its real state is unknown. Report it once instead of replaying docker commands.
        self.reconcile_stack_claims()

    def stack_claim(self, identity):
        return self.home/'claims'/(identity + '.json')

    def reconcile_stack_claims(self):
        """Settle claims left behind by an earlier executor process; never replay them."""
        try:
            claims = sorted((self.home/'claims').glob('*.json'))
        except OSError:
            return
        for claim in claims:
            try:
                body = read(claim)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if not isinstance(body, dict) or body.get('action') != 'stack':
                continue
            target = self.home/'responses'/claim.name
            try:
                if target.exists() and read(target).get('status') in STACK_TERMINAL:
                    continue
            except (OSError, json.JSONDecodeError, ValueError):
                pass
            try:
                write(target, dict(id=claim.stem, kind='stack', project=body.get('project'),
                                   group=(body.get('options') or {}).get('group'),
                                   operation=(body.get('options') or {}).get('operation'),
                                   status='interrupted', stage='claimed-without-executor',
                                   observed_at=time.time(), error=STACK_CLAIM_REASON))
            except OSError:
                continue

    def tick(self):
        write(self.home/'heartbeat.json', {'pid': os.getpid(), 'updated_at': time.time(), 'protocol': 1})
        for path in sorted((self.home/'requests').glob('*.json')):
            identity = path.stem
            if not re.fullmatch(r'[0-9a-f]{32}', identity):
                continue
            target = self.home/'responses'/path.name
            observing = False
            try:
                claim = self.home/'claims'/path.name
                # Reconcile accepted jobs even if an older observer reported an error.
                # Rejected, unclaimed terminal requests must never become executable.
                if not claim.exists() and target.exists() and read(target).get('status') in TERMINAL:
                    continue
                request = read(path)
                # Admission can be revoked without changing already accepted identities/history.
                intent = validate(request, identity, self.projects, admission=not claim.exists())
                if intent['action'] == 'list-refs' and not claim.exists():
                    # Read-only capability: answered inline, without a claim or a job, so it
                    # can never execute, deploy or replay. The terminal guard above stops
                    # repeated work once an answer exists.
                    if not time.time() < request['expires_at'] <= time.time()+120:
                        raise ValueError('Request expired or invalid deadline')
                    self.answer_refs(identity, request, target)
                    continue
                if intent['action'] == 'stack':
                    # Every lifecycle answer is settled once: a timed out or interrupted
                    # operation must never be turned back into 'running' by a later tick,
                    # and must never be executed a second time.
                    if target.exists() and read(target).get('status') in STACK_TERMINAL:
                        continue
                    if not claim.exists() and not time.time() < request['expires_at'] <= time.time()+120:
                        raise ValueError('Request expired or invalid deadline')
                    self.answer_stack(identity, request, target)
                    continue
                if intent['action'] == 'versions' and not claim.exists():
                    if not time.time() < request['expires_at'] <= time.time()+120:
                        raise ValueError('Request expired or invalid deadline')
                    self.answer_versions(identity, request, target)
                    continue
                if intent['action'] == 'set-source' and not claim.exists():
                    if not time.time() < request['expires_at'] <= time.time()+120:
                        raise ValueError('Request expired or invalid deadline')
                    self.answer_source_edit(identity, request, target)
                    continue
                if claim.exists():
                    saved = read(claim)
                    if saved and saved != intent:
                        raise ValueError('Job identity mismatch')
                    try:
                        job = self.pipeline.get_job(identity)
                    except json.JSONDecodeError:
                        raise
                    except ValueError:
                        # A claim recorded before a rejected submission has no execution
                        # record. Keep the recorded rejection instead of replacing it with
                        # 'interrupted', which would hide the real admission error.
                        recorded = read(target) if target.exists() else None
                        if not (recorded and recorded.get('status') in TERMINAL):
                            write(target, dict(id=identity, status='interrupted', error='Claim has no execution record; reconcile manually; do not retry automatically'))
                        continue
                    if any(job.get(key) != value for key, value in intent.items()):
                        raise ValueError('Job identity mismatch')
                else:
                    if not time.time() < request['expires_at'] <= time.time()+120:
                        raise ValueError('Request expired or invalid deadline')
                    if intent['action'] in ('build-deploy', 'deploy', 'rollback'):
                        # Real mutual exclusion, both directions: a release never starts
                        # while a lifecycle operation holds the shared lock, and a
                        # lifecycle operation never starts while a release holds it
                        # (stack_groups takes the same lock).
                        holder = self.lifecycle_lock()
                        if holder:
                            raise ValueError('Another operation holds the release lock (' + holder + '); wait for it to finish')
                        if any(state['status'] == 'running' for state in self.stack_runs.values()):
                            raise ValueError('A stack lifecycle operation is in progress; wait for it to finish before releasing')
                    # Claim is durable BEFORE enqueue. A crash here must never replay automatically.
                    write(claim, intent)
                    job = self.pipeline.submit_once(identity, request['project'], request['action'], **request['options'])
                observing = True
                if job['status'] in TERMINAL and target.exists():
                    previous = read(target)
                    if (previous.get('terminal_verified') is True
                            and all(previous.get(key) == job.get(key) for key in (*intent, 'status'))):
                        continue
                result = dict(job)
                result['observed_at'] = time.time()
                if self.root and job.get('version'):
                    manifest = self.root/'releases/manifests'/job['project']/(job['version']+'.json')
                    if manifest.is_file():
                        data = read(manifest)
                        result['images'] = data['images']
                        result['manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
                if self.root and job['status'] in TERMINAL:
                    current = read(self.root/'releases/current.compose.json')
                    result['current_release'] = current.get('x-release-state', {}).get(job['project'])
                if job['status'] in TERMINAL:
                    result['terminal_verified'] = True
                write(target, result)
            except (OSError, json.JSONDecodeError):
                print(f'Transient queue IO failure for {identity}; reconciliation will retry', file=sys.stderr, flush=True)
                continue
            except ValueError as error:
                if observing:
                    print(f'Result enrichment unavailable for {identity}; reconciliation will retry', file=sys.stderr, flush=True)
                    continue
                # No raw command/config output or credentials in protocol errors.
                write(target, dict(id=identity, status='failed', error=str(error)[:500]))
            except Exception:
                if not observing:
                    raise
                print(f'Result observation unavailable for {identity}; reconciliation will retry', file=sys.stderr, flush=True)

    def answer_refs(self, identity, request, target):
        """Answer a read-only ref listing. Failures are reported as failures, never as an empty list."""
        options = request['options']
        try:
            refs = self.pipeline.list_refs(request['project'], options.get('ref') or None)
        except (ValueError, RuntimeError, TimeoutError, OSError) as error:
            write(target, dict(id=identity, status='failed', kind='refs', error=str(error)[:500]))
            return
        result = dict(id=identity, status='succeeded', kind='refs', stage='refs', project=request['project'],
                      observed_at=time.time(), **refs)
        write(target, result)

    def lifecycle_lock(self):
        """Describe whoever holds the shared release/lifecycle lock, if anyone does."""
        if self.root is None:
            return None
        try:
            body = (self.root / 'releases/.release.lock').read_text(encoding='utf-8').strip()
        except OSError:
            return None
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                subject = data.get('project') or ','.join(data.get('groups') or []) or data.get('services') or ''
                return '{0} {1} pid={2}'.format(data.get('kind'), subject, data.get('pid')).strip()
        except ValueError:
            pass
        return 'legacy holder ' + (body or '?')

    def stack_runner(self, identity, request):
        """Run a lifecycle operation on its own thread so heartbeats keep flowing."""
        state = self.stack_runs.get(identity)
        if state:
            return state
        claim = self.stack_claim(identity)
        if claim.exists():
            # A claim without a live run in this process belongs to an earlier executor
            # (or an already accepted request). Replaying docker here would be a guess.
            return {'status': 'interrupted', 'stage': 'claimed-without-executor', 'output': [], 'error': STACK_CLAIM_REASON,
                    'started_at': time.time()}
        operation, group = request['options']['operation'], request['options']['group']
        state = {'status': 'running', 'stage': 'stack-' + operation, 'output': [], 'error': None, 'started_at': time.time()}

        def settle(status, **extra):
            # A settled 'timed_out' verdict is the operator's record of an unknown result;
            # stop the docker call but do not rewrite what was already reported.
            if state.get('status') == 'timed_out' and status != 'timed_out':
                return
            state.update(status=status, **extra)

        self.stack_runs[identity] = state
        if self.root is None:
            settle('failed', stage='error', error='A platform root is required for lifecycle operations')
            return state
        sys.path.insert(0, str(self.root / 'scripts'))
        import stack_groups

        def run(command):
            state['output'].append(' '.join(str(item) for item in command))
            try:
                result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                        errors='replace', shell=False, timeout=self.command_timeout)
            except subprocess.TimeoutExpired:
                raise stack_groups.StackError('docker compose timed out after {0:.0f}s; the command was stopped and the operation state is unknown'.format(self.command_timeout))
            tail = (result.stdout or '') + (result.stderr or '')
            if tail.strip():
                state['output'].extend(line for line in tail.strip().splitlines()[-20:])
            if result.returncode:
                raise stack_groups.StackError('docker compose exited {0}: {1}'.format(result.returncode, tail.strip()[-300:]))
            return result

        def work():
            try:
                stack_groups.Stack(self.root, run=run).execute(operation.capitalize(), groups=[group])
                settle('succeeded', stage='done')
            except Exception as error:  # noqa: BLE001 - reported to the operator, never raised into the tick loop
                settle('failed', stage='error', error=str(error)[:500])

        # Durable claim BEFORE any docker command: if the executor dies here, startup
        # reconciliation reports the operation as interrupted instead of replaying it.
        write(claim, dict(id=identity, project=request['project'], action='stack', kind='stack',
                          options=dict(request['options']), claimed_at=time.time()))
        threading.Thread(target=work, daemon=True, name='komodo-stack-' + identity[:8]).start()
        return state

    def answer_stack(self, identity, request, target):
        state = self.stack_runner(identity, request)
        if state['status'] == 'running' and time.time() > state['started_at'] + self.stack_timeout:
            state.update(status='timed_out', stage='timeout',
                         error='Lifecycle operation exceeded {0:.0f}s; the result is unknown. Re-check with the same entry '
                               '(operation=status) before resubmitting'.format(self.stack_timeout))
        result = {'id': identity, 'kind': 'stack', 'project': request['project'], 'group': request['options']['group'],
                  'operation': request['options']['operation'], 'status': state['status'], 'stage': state['stage'],
                  'observed_at': time.time(), 'output': state['output'][-40:], 'job': identity[:8]}
        if state['error']:
            result['error'] = state['error']
        write(target, result)

    def answer_versions(self, identity, request, target):
        """Read-only version view: repository HEAD, candidate, release, running images."""
        try:
            versions = self.pipeline.versions(request['project'])
        except (ValueError, RuntimeError, TimeoutError, OSError) as error:
            write(target, dict(id=identity, status='failed', kind='versions', error=str(error)[:500]))
            return
        write(target, dict(id=identity, status='succeeded', kind='versions', stage='versions', project=request['project'],
                           observed_at=time.time(), **versions))

    def answer_source_edit(self, identity, request, target):
        """Reviewed source edit; the platform module validates, backs up and writes atomically."""
        options = request['options']
        payload = {'mode': options['mode'], 'refs': list(options['refs']), 'default_ref': options['default_ref']}
        if options['remote']:
            payload['remote'] = options['remote']
        try:
            report = self.pipeline.apply_source(request['project'], payload)
        except (ValueError, RuntimeError, TimeoutError, OSError) as error:
            write(target, dict(id=identity, status='failed', kind='source-edit', error=str(error)[:500]))
            return
        holder = self.lifecycle_lock()
        if holder:
            report['lock_holder'] = holder
        write(target, dict(id=identity, status='succeeded', kind='source-edit', stage='applied',
                           observed_at=time.time(), **report))

    def active(self):
        # Observation files are never authoritative for stopping an executor, but an
        # in-flight lifecycle operation is: stopping here would abandon a half-applied
        # start/stop with no durable executor able to finish or report it.
        if any(state.get('status') == 'running' for state in self.stack_runs.values()):
            return True
        try:
            return any(job['status'] in {'queued', 'running'} for job in self.pipeline.list_jobs())
        except Exception:
            print('Execution state unavailable; deferring shutdown', file=sys.stderr, flush=True)
            return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--queue', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.root/'services/project-console'))
    from pipeline import Pipeline, exclusive
    args.queue.mkdir(parents=True, exist_ok=True)
    with exclusive(args.queue/'worker.lock'):
        # Do not recover another worker's currently queued/running tasks on startup.
        jobs = args.root/'.local/project-console/jobs'
        if any(read(path)['status'] in {'queued', 'running'} for path in jobs.glob('*/job.json')):
            raise RuntimeError('Existing pipeline is active; start this worker only while idle')
        bridge = Bridge(args.queue, Pipeline(args.root), args.root)
        write(args.queue/'worker.json', {'pid': os.getpid(), 'started_at': time.time()})
        while True:
            try:
                bridge.tick()
            except OSError:
                print('Queue IO unavailable; retrying without changing execution state', file=sys.stderr, flush=True)
            if (args.queue/'stop').exists():
                if not bridge.active():
                    (args.queue/'stop').unlink()
                    break
            time.sleep(2)


if __name__ == '__main__':
    main()
