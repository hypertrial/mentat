"""A seeded surface review is narrow, durable, and still passes normal gates."""

import json
import sqlite3
from contextlib import closing

import pytest
from test_autonomy import Agent, GitHub, Worker, _valid_publication_spec, git
from test_autonomy import repo as repo
from test_autonomy_bootstrap import _setup

from mentat.autonomy import Coordinator
from mentat.autonomy_state import Store


def test_cli_forwards_explicit_surface_without_changing_default(repo, monkeypatch):
    from test_autonomy_cli import invoke

    from mentat import autonomy_cli

    calls = []

    class FakeCoordinator:
        def __init__(self, path, store, agent, worker, github):
            self.store, self.session_id = store, None

        def run(self, *, resume=None, spec_surface=None):
            calls.append((resume, spec_surface))
            self.session_id = self.store.new_session(str(repo), 'base', 'head', {}, 1, 1000)
            return self.store.snapshot(self.session_id)

    monkeypatch.setattr(autonomy_cli, 'Coordinator', FakeCoordinator)
    for options in ((), ('--spec-surface', 'src.py')):
        result = invoke(repo, 'run', '--repo', str(repo), *options)
        assert result.exit_code == 0, result.output
    assert calls == [(None, None), (None, 'src.py')]


@pytest.fixture
def seeded(repo, tmp_path):
    (repo / 'src.py').write_text('def value(x): return x\n')
    store, config = _setup(repo, tmp_path)
    _valid_publication_spec(repo)
    fas_path = repo / '.mentat/fas.json'
    fas = json.loads(fas_path.read_text())
    surface = next(row for row in fas['surfaces'] if row['path'] == 'src.py')
    surface.update(status='mapped', requirement_ids=[fas['requirements'][0]['id']], note='Original observation')
    fas_path.write_text(json.dumps(fas))
    git(repo, 'commit', '-qam', 'existing mapped surface')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', base)
    surface['note'] = f'At task input revision {base}, value returns its input.'
    fas_path.write_text(json.dumps(fas))
    git(repo, 'commit', '-qam', 'seed one observation')
    return repo, store, config, base, git(repo, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('fault', ['absent', 'unrelated', 'multiple', 'requirement', 'claim', 'source',
                                   'unmapped', 'no_change', 'mode', 'reordered'])
def test_scoped_run_rejects_invalid_seed_before_bootstrap(seeded, fault):
    repo, store, _, _, _ = seeded
    fas_path = repo / '.mentat/fas.json'
    fas = json.loads(fas_path.read_text())
    selected = 'src.py'
    if fault == 'absent':
        selected = 'missing.py'
    elif fault == 'unrelated':
        selected = '.mentat/workspace.json'
    elif fault == 'multiple':
        fas['surfaces'][1]['note'] = 'unrelated surface change'
    elif fault == 'requirement':
        fas['requirements'][0]['statement'] = 'Changed requirement'
    elif fault == 'claim':
        fas['claims'].append({'id': 'unexpected'})
    elif fault == 'source':
        (repo / 'src.py').write_text('def value(): return 2\n')
    elif fault == 'unmapped':
        next(row for row in fas['surfaces'] if row['path'] == 'src.py').update(status='gap', requirement_ids=[])
    elif fault == 'mode':
        fas_path.chmod(0o755)
    elif fault == 'reordered':
        fas['surfaces'].reverse()
    else:
        git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    if fault not in {'absent', 'unrelated', 'no_change'}:
        fas_path.write_text(json.dumps(fas))
        git(repo, 'add', '.')
        git(repo, 'commit', '-qm', 'invalid additional change')
    with pytest.raises(ValueError):
        Coordinator(repo, store, Agent(), Worker(), GitHub()).run(spec_surface=selected)
    with closing(store._connect()) as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0


def test_scoped_run_rejects_gitlink_change_hidden_by_diff_config(seeded):
    repo, store, _, original_base, original_head = seeded
    git(repo, 'checkout', '-q', '--detach', original_base)
    fas_path = repo / '.mentat/fas.json'
    fas = json.loads(fas_path.read_text())
    fas['surfaces'].append({'path': 'vendor', 'status': 'gap', 'requirement_ids': []})
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.mentat/fas.json')
    git(repo, 'update-index', '--add', '--cacheinfo', f'160000,{original_base},vendor')
    git(repo, 'commit', '-qm', 'add synthetic gitlink to base')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', base)
    next(row for row in fas['surfaces'] if row['path'] == 'src.py')['note'] = 'Changed observation'
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.mentat/fas.json')
    git(repo, 'update-index', '--cacheinfo', f'160000,{original_head},vendor')
    git(repo, 'commit', '-qm', 'change observation and synthetic gitlink')
    head = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'config', 'diff.ignoreSubmodules', 'all')
    assert git(repo, 'diff', '--name-only', base, head) == '.mentat/fas.json'
    assert set(git(repo, 'diff', '--ignore-submodules=none', '--name-only', base, head).splitlines()) == {
        '.mentat/fas.json', 'vendor'}
    with pytest.raises(ValueError, match='FAS-only committed change'):
        Coordinator(repo, store, Agent(), Worker(), GitHub()).run(spec_surface='src.py')
    with closing(store._connect()) as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0


def test_scoped_bootstrap_is_atomic_and_resume_retains_selection(seeded, monkeypatch):
    repo, store, config, base, head = seeded
    with closing(store._connect()) as db:
        db.execute("CREATE TRIGGER fail_scoped_job BEFORE INSERT ON jobs WHEN NEW.item_id = 'cas.inventory' "
                   "BEGIN SELECT RAISE(ABORT, 'scoped crash'); END")
    with pytest.raises(sqlite3.Error, match='scoped crash'):
        Coordinator(repo, store, Agent(), Worker(), GitHub()).run(spec_surface='src.py')
    with closing(store._connect()) as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        db.execute('DROP TRIGGER fail_scoped_job')
    monkeypatch.setattr(Store, 'claim_job', lambda *args, **kwargs: None)
    snapshot = Coordinator(repo, store, Agent(), Worker(), GitHub()).run(spec_surface='src.py')
    start = next(e['payload'] for e in snapshot['events'] if e['kind'] == 'session_started')
    job = next(j for j in snapshot['jobs'] if j['payload']['kind'] == 'generate_spec')
    assert start['spec_surface'] == 'src.py'
    assert start['base'] == base and start['head'] == head
    assert job['payload']['targetfiles'] == ['src.py']
    assert sum(j['payload']['kind'] == 'source_check' for j in snapshot['jobs']) == len(config['source_checks'])
    resumed = Coordinator(repo, Store(store.path), Agent(), Worker(), GitHub()).run(resume=snapshot['session']['id'])
    assert [j for j in resumed['jobs'] if j['payload']['kind'] == 'generate_spec'] == [job]
    assert next(e['payload'] for e in resumed['events'] if e['kind'] == 'session_started') == start


@pytest.mark.parametrize('tamper', ['remove_scope', 'change_scope', 'change_targets'])
def test_resume_rejects_rebound_durable_scope(seeded, monkeypatch, tamper):
    repo, store, _, _, _ = seeded
    monkeypatch.setattr(Store, 'claim_job', lambda *args, **kwargs: None)
    snapshot = Coordinator(repo, store, Agent(), Worker(), GitHub()).run(spec_surface='src.py')
    session = snapshot['session']['id']
    with closing(store._connect()) as db:
        if tamper == 'change_targets':
            job = next(j for j in snapshot['jobs'] if j['payload']['kind'] == 'generate_spec')
            payload = dict(job['payload'], targetfiles=['.mentat/workspace.json'])
            db.execute('UPDATE jobs SET payload = ? WHERE id = ?', (json.dumps(payload), job['id']))
        else:
            event = next(e for e in snapshot['events'] if e['kind'] == 'session_started')
            payload = dict(event['payload'])
            if tamper == 'remove_scope':
                del payload['spec_surface']
            else:
                payload['spec_surface'] = '.mentat/workspace.json'
            db.execute('UPDATE events SET payload = ? WHERE seq = ?', (json.dumps(payload), event['seq']))
        db.commit()
    with pytest.raises(ValueError):
        Coordinator(repo, Store(store.path), Agent(), Worker(), GitHub()).run(resume=session)


@pytest.mark.parametrize('mutation', ['none', 'reported', 'direct'])
def test_scoped_coordinator_runs_reviews_and_rejects_model_edits(seeded, mutation):
    repo, store, _, base, head = seeded
    calls = []

    class ReviewAgent(Agent):
        def run(self, worktree, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            calls.append((job['id'], kind, payload))
            if kind in {'generate_spec', 'generate_verification'}:
                changes = []
                if kind == 'generate_spec' and mutation == 'reported':
                    changes = [{'path': '.mentat/fas.json', 'operation': 'write', 'content': '{}'}]
                elif kind == 'generate_spec' and mutation == 'direct':
                    (worktree / 'src.py').write_text('def value(): return 999\n')
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'No changes', 'findings': [],
                    'verification_requests': [], 'changes': changes}}
            return super().run(worktree, job, output, timeout_seconds=timeout_seconds)

    worker, github = Worker(), GitHub()
    snapshot = Coordinator(repo, store, ReviewAgent(), worker, github).run(spec_surface='src.py')
    generators = [payload for _, kind, payload in calls if kind == 'generate_spec']
    assert generators and all(payload.get('targetfiles') == ['src.py'] for payload in generators)
    assert git(repo, 'diff', '--name-only', base, head) == '.mentat/fas.json'
    if mutation != 'none':
        assert not github.published and not github.merged, snapshot
        return
    assert github.published == [head], snapshot
    assert len(github.merged) == 1
    merged_head, merged_base, gates = github.merged[0]
    assert (merged_head, merged_base) == (head, base)
    assert gates['checks'] and all(check['head'] == head and check['base'] == base for check in gates['checks'])
    assert len({review['reviewer_id'] for review in gates['reviews']}) == 2
    assert worker.commands and ['check'] in worker.commands
    assert all(revision == head for revision in worker.revisions)
    for kind in ('review_spec', 'review_verification', 'review_final_source', 'review_final'):
        ids = {job_id for job_id, stage, _ in calls if stage == kind}
        assert len(ids) >= 2, (kind, snapshot)
    final_source = [payload for _, kind, payload in calls if kind == 'review_final_source']
    assert all({item['path'] for item in payload['source_batch']['obligations']} == {'src.py'}
               for payload in final_source)
    final = [payload for _, kind, payload in calls if kind == 'review_final']
    assert all(payload['proposal']['parent'] == base for payload in final)
    for payload in final:
        coverage = payload['source_coverage']
        assert coverage['bindings']['required_paths'] == ['src.py']
        assert {gap['path'] for gap in coverage['gaps']} == {
            '.mentat/workspace.json', '.mentat/fas.json', 'openspec/specs/app/spec.md'}
