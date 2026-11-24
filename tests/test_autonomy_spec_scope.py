"""Correction authority stays bound to the admitted, independently reviewed draft."""

import hashlib
import json

import pytest
from test_autonomy import Agent, _review_capacity_repo, git, run
from test_autonomy import repo as repo

from mentat.autonomy_state import record_digest


def response(job, changes=None):
    return {'status': 'succeeded', 'session_id': job['id'], 'result': {
        'decision': 'accept', 'summary': 'observed source', 'findings': [],
        'verification_requests': [], 'changes': changes or []}}


@pytest.mark.parametrize('operation', ['write', 'patch_fas'])
def test_final_round_rejects_overbroad_correction_before_commit_and_retries(repo, tmp_path, operation):
    requirements, paths = _review_capacity_repo(repo)
    for path in paths:
        (repo / path).write_text('# ' + 'x' * 55_000 + '\n')
    git(repo, 'commit', '-qam', 'source exceeds one generation context')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    accepted = git(repo, 'rev-parse', 'HEAD')
    calls = []

    class CorrectionAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
            calls.append((kind, job['id'], job['attempt'], payload, context, git(repo, 'rev-parse', 'HEAD')))
            if kind == 'generate_spec':
                if payload.get('targetfiles') and paths[0] not in payload['targetfiles']:
                    return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                        'decision': 'defer', 'summary': 'Remaining source requires independent review',
                        'findings': [], 'verification_requests': []}}
                path = repo / '.mentat/fas.json'
                current = path.read_bytes()
                fas = json.loads(current)
                round_number = payload.get('round', 0)
                # Two real review cycles put this correction at the last round.
                overbroad = round_number == 2 and job['attempt'] == 1
                selected = paths if overbroad else paths[:1]
                edits = []
                for row in fas['surfaces']:
                    if row['path'] in selected:
                        original = dict(row)
                        row.update(status='mapped', requirement_ids=[requirements[0]['id']],
                                   note=f'Actual observation, review round {round_number}')
                        edits.append({'collection': 'surfaces', 'key': row['path'],
                                      'expected_digest': record_digest(original), 'record': dict(row)})
                if operation == 'write':
                    changes = [{'path': '.mentat/fas.json', 'operation': 'write', 'content': json.dumps(fas)}]
                else:
                    patch = {'schema_version': 1, 'expected_sha256': hashlib.sha256(current).hexdigest(),
                             'edits': edits}
                    changes = [{'path': '.mentat/fas.json', 'operation': 'patch_fas', 'content': json.dumps(patch)}]
                return response(job, changes)
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'review_spec' and payload['proposal'].get('round', 0) < 2:
                if job['item_id'].endswith('.1'):
                    result['result']['decision'] = 'revise'
                    result['result']['findings'] = [{'summary': 'Clarify the reviewed remote URL token.'}]
            return result

    snapshot, _, _ = run(repo, tmp_path, agent=CorrectionAgent(), concurrency=3)
    generations = [row for row in calls if row[0] == 'generate_spec' and row[3].get('round') == 2]
    assert len(generations) == 2, snapshot
    bad, good = generations
    assert bad[1] == good[1] and (bad[2], good[2]) == (1, 2)
    assert bad[5] == good[5] and bad[3]['review_base'] == good[3]['review_base'] == accepted
    assert 'Clarify the reviewed remote URL token.' in json.dumps(good[3])
    assert bad[4]['correction_scope'] == good[4]['correction_scope']
    assert 'correction_scope' not in good[3]
    assert good[3]['correction_scope_id'] == good[4]['correction_scope']['id']
    assert 'read-only' in good[3]['prompt']
    assert good[3]['prompt'].count('\"correction_scope\"') == 1
    jobs = [j for j in snapshot['jobs'] if j['id'] == bad[1]]
    assert len(jobs) == 1 and jobs[0]['attempt'] == 2
    corrected_head = jobs[0]['outcome']['head']
    assert corrected_head != bad[5]
    corrected = json.loads(git(repo, 'show', f'{corrected_head}:.mentat/fas.json'))
    assert next(s for s in corrected['surfaces'] if s['path'] == paths[0])['note'].endswith('2')
    assert all(next(s for s in corrected['surfaces'] if s['path'] == p)['status'] == 'gap' for p in paths[1:])
    reviews = [row for row in calls if row[0] == 'review_spec'
               and row[3]['proposal'].get('head') == corrected_head]
    assert len(reviews) == 2 and len({row[1] for row in reviews}) == 2
    assert all(row[3]['proposal']['parent'] == accepted for row in reviews)
    assert any(row[0] == 'generate_spec' and row[3].get('targetfiles')
               and any(p in row[3]['targetfiles'] for p in paths[1:]) for row in calls)

    # Reuse the real durable review and scope, but construct a sibling of the
    # accepted checkpoint with an unrelated mapped surface in its FAS.
    # Matching the job's durable enqueue event and ancestry must not authorize it.
    from test_autonomy import GitHub, Worker

    from mentat.autonomy import Coordinator, _git_paths, current_tool_identity, digest, inventory
    from mentat.autonomy_spec_scope import authenticate
    from mentat.autonomy_spec_scope import digest as scope_digest
    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'state' / 'state.db')
    controller = Coordinator(repo, store, Agent(), Worker(), GitHub())
    controller.session_id = snapshot['session']['id']
    controller.authority_digest = digest(json.loads((store.path.parent / 'policy.json').read_text()))
    controller.tool_identity = current_tool_identity()
    original = jobs[0]
    assert authenticate(controller, original, inventory, _git_paths) == original['payload']['correction_scope']
    sibling = tmp_path / 'unrelated-sibling'
    git(repo, 'worktree', 'add', '--detach', str(sibling), accepted)
    sibling_fas = sibling / '.mentat/fas.json'
    data = json.loads(sibling_fas.read_text())
    next(row for row in data['surfaces'] if row['path'] == paths[1]).update(
        status='mapped', requirement_ids=[requirements[0]['id']])
    sibling_fas.write_text(json.dumps(data))
    git(sibling, 'commit', '-qam', 'unrelated sibling mapping')
    foreign = git(sibling, 'rev-parse', 'HEAD')
    altered = {**original['payload'], 'target_head': foreign}
    injected = store.enqueue_job(controller.session_id, 'scl', 'implementer', 'foreign-sibling',
                                 'foreign-input', altered)
    durable = next(j for j in store.snapshot(controller.session_id)['jobs'] if j['id'] == injected)
    with pytest.raises(ValueError, match='scope'):
        authenticate(controller, durable, inventory, _git_paths)

    # Even self-consistently rehashed scope payloads cannot rebind real review
    # authority to a different endpoint, cohort, or review decision/job.
    for field in ('accepted', 'draft', 'review_base', 'cohort', 'decision_id', 'review_job_id'):
        forged = json.loads(json.dumps(original['payload']))
        forged_scope = forged['correction_scope']
        forged_scope['binding'][field] = foreign if field in {'accepted', 'draft', 'review_base'} else 'foreign-' + field
        forged_scope['id'] = scope_digest({key: value for key, value in forged_scope.items() if key != 'id'})
        forged_id = store.enqueue_job(controller.session_id, 'scl', 'implementer',
                                      'forged-' + field, 'input-' + field, forged)
        forged_job = next(j for j in store.snapshot(controller.session_id)['jobs'] if j['id'] == forged_id)
        with pytest.raises(ValueError):
            authenticate(controller, forged_job, inventory, _git_paths)


def test_edit_envelope_contains_only_actual_record_deltas_and_linked_documents():
    import copy

    from mentat.autonomy_spec_scope import derive, validate_delta

    before = {'requirements': [{'id': 'r1', 'spec_path': 'openspec/specs/a/spec.md', 'statement': 'old'},
                               {'id': 'r2', 'spec_path': 'openspec/specs/b/spec.md', 'statement': 'other'}],
              'claims': [{'id': 'c1', 'requirement_ids': ['r1']}],
              'surfaces': [{'path': 'worker.py', 'status': 'gap', 'requirement_ids': []},
                           {'path': 'other.py', 'status': 'gap', 'requirement_ids': []}]}
    admitted = copy.deepcopy(before)
    admitted['requirements'][0]['statement'] = 'reviewed'
    admitted['surfaces'][0]['status'] = 'mapped'
    admitted['surfaces'][0]['requirement_ids'] = ['r1']
    binding = {'accepted': 'accepted', 'draft': 'draft', 'review_base': 'base',
               'cohort': 'cohort', 'decision_id': 'decision', 'review_job_id': 'review'}
    scope = derive(before, admitted, ['.mentat/fas.json'], binding)
    assert scope['records'] == {'requirements': ['r1'], 'claims': [], 'surfaces': ['worker.py']}
    assert scope['files'] == ['openspec/specs/a/spec.md']
    corrected = copy.deepcopy(admitted)
    corrected['requirements'][0]['statement'] = 'corrected'
    corrected['surfaces'][0]['note'] = 'corrected review observation'
    validate_delta(scope, admitted, corrected, ['.mentat/fas.json', 'openspec/specs/a/spec.md'])
    for collection, mutation in [
        ('requirements', lambda rows: rows[1].update(statement='unrelated')),
        ('claims', lambda rows: rows[0].update(statement='unrelated')),
        ('surfaces', lambda rows: rows[1].update(status='mapped', requirement_ids=['r1'])),
        ('surfaces', lambda rows: rows.append({'path': 'inserted.py', 'status': 'gap', 'requirement_ids': []})),
        ('surfaces', lambda rows: rows.pop(1)),
    ]:
        changed = copy.deepcopy(corrected)
        mutation(changed[collection])
        with pytest.raises(ValueError, match='scope'):
            validate_delta(scope, admitted, changed, ['.mentat/fas.json'])
    with pytest.raises(ValueError, match='scope'):
        validate_delta(scope, admitted, corrected, ['openspec/specs/b/spec.md'])
    for path in ['../escape', '/absolute', 'src.py', 'openspec/specs/../../escape']:
        with pytest.raises(ValueError):
            derive(before, admitted, [path], binding)
    assert scope['id'] == derive(before, admitted, ['.mentat/fas.json'], binding)['id']
    for field in binding:
        rebound = {**binding, field: binding[field] + '-other'}
        assert derive(before, admitted, ['.mentat/fas.json'], rebound)['id'] != scope['id']


def test_inventory_addition_is_limited_to_new_allowed_spec_file():
    from mentat.autonomy_spec_scope import derive, validate_delta

    before = {'requirements': [], 'claims': [], 'surfaces': []}
    binding = {'accepted': 'accepted', 'draft': 'draft', 'review_base': 'base',
               'cohort': 'cohort', 'decision_id': 'decision', 'review_job_id': 'review'}
    new_path = 'openspec/specs/new/spec.md'
    scope = derive(before, before, [new_path], binding)
    result = {'requirements': [], 'claims': [], 'surfaces': [
        {'path': new_path, 'status': 'unexamined', 'requirement_ids': []}]}
    validate_delta(scope, before, result, [new_path, '.mentat/fas.json'])
    for record in [
        {'path': new_path, 'status': 'mapped', 'requirement_ids': ['r']},
        {'path': new_path, 'status': 'gap', 'requirement_ids': []},
        {'path': new_path, 'status': 'unexamined', 'requirement_ids': [], 'note': 'extra'},
        {'path': 'src/unrelated.py', 'status': 'unexamined', 'requirement_ids': []},
    ]:
        with pytest.raises(ValueError, match='scope'):
            validate_delta(scope, before, {**result, 'surfaces': [record]}, [new_path, '.mentat/fas.json'])


@pytest.mark.parametrize('scoped', [True, False])
def test_capacity_recovery_defer_preserves_scope_in_smaller_frontier(repo, tmp_path, scoped):
    from test_autonomy import GitHub, Worker

    from mentat.autonomy import Coordinator, default_config
    from mentat.autonomy_spec_scope import derive, validate_delta
    from mentat.autonomy_state import Store

    head = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'continuation' / 'state.db')
    coordinator = Coordinator(repo, store, Agent(), Worker(), GitHub())
    coordinator.config = default_config(repo)
    coordinator.session_id = store.new_session(str(repo), head, head, coordinator.config, now=1, deadline=7200)
    before = {'requirements': [], 'claims': [], 'surfaces': [
        {'path': 'src.py', 'status': 'gap', 'requirement_ids': []}]}
    after = {'requirements': [], 'claims': [], 'surfaces': [
        {'path': 'src.py', 'status': 'mapped', 'requirement_ids': ['r']}]}
    scope = derive(before, after, ['.mentat/fas.json'], {
        'accepted': head, 'draft': 'draft', 'review_base': head, 'cohort': 'cohort',
        'decision_id': 'decision', 'review_job_id': 'review'})
    payload = {'kind': 'generate_spec', 'target_head': head, 'accepted_spec_base': head,
               'review_base': head, 'targetfiles': ['src.py', 'other.py'], 'chunk': 0,
               'round': 2, 'batch_limit': 3}
    if scoped:
        payload['correction_scope'] = scope
    job = {'id': 'prior', 'item_id': 'cas.inventory', 'loop': 'scl', 'payload': payload}
    coordinator._advance(job, {'status': 'succeeded', 'head': head,
                              'context_pending': ['other.py'], 'context_included': ['src.py'],
                              'result': {'decision': 'defer', 'summary': 'smaller context'}})
    successor = next(j for j in Store(store.path).snapshot(coordinator.session_id)['jobs']
                     if j['payload'].get('kind') == 'generate_spec')
    assert successor['payload']['targetfiles'] == ['other.py']
    assert successor['payload']['round'] == 2
    assert successor['payload'].get('correction_scope') == (scope if scoped else None)
    if scoped:
        broad = {**after, 'surfaces': [*after['surfaces'],
                 {'path': 'other.py', 'status': 'mapped', 'requirement_ids': ['r']}]}
        with pytest.raises(ValueError, match='scope'):
            validate_delta(successor['payload']['correction_scope'], after, broad, ['.mentat/fas.json'])


def test_execution_identity_rejects_changed_scope_helper(tmp_path, monkeypatch):
    from pathlib import Path

    from mentat import autonomy_runtime, autonomy_spec_scope

    changed = tmp_path / 'autonomy_spec_scope.py'
    changed.write_bytes(Path(autonomy_spec_scope.__file__).read_bytes() + b'\n# changed after import\n')
    monkeypatch.setattr(autonomy_spec_scope, '__file__', str(changed))
    with pytest.raises(ValueError, match='changed after import'):
        autonomy_runtime.execution_identity()


def test_bootstrap_review_correction_retries_without_allowing_new_record(repo, tmp_path):
    import uuid

    base = git(repo, 'rev-parse', 'HEAD')
    assert git(repo, 'ls-tree', '-r', '--name-only', base, '--', '.mentat/fas.json') == ''
    foreign_id = str(uuid.uuid4())
    calls = []

    class BootstrapAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
            calls.append((kind, job['id'], job['attempt'], payload, git(repo, 'rev-parse', 'HEAD'), context))
            if kind == 'generate_spec' and payload.get('round') == 1:
                fas_path = repo / '.mentat/fas.json'
                fas = json.loads(fas_path.read_text())
                surface = next(row for row in fas['surfaces'] if row['path'] == 'src.py')
                surface['note'] = 'Clarified review observation'
                changes = []
                if job['attempt'] == 1:
                    extra = dict(fas['requirements'][0], id=foreign_id, statement='Unrelated extra intent')
                    fas['requirements'].append(extra)
                    spec_path = extra['spec_path']
                    changes.append({'path': spec_path, 'operation': 'write',
                                    'content': (repo / spec_path).read_text() + '\n' + foreign_id})
                changes.append({'path': '.mentat/fas.json', 'operation': 'write',
                                'content': json.dumps(fas)})
                return response(job, changes)
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if (kind == 'review_spec' and payload['proposal'].get('round', 0) == 0
                    and job['item_id'].endswith('.1')):
                result['result']['decision'] = 'revise'
                result['result']['findings'] = [{'summary': 'Clarify the bootstrap source observation.'}]
            return result

    snapshot, _, _ = run(repo, tmp_path, agent=BootstrapAgent())
    attempts = [call for call in calls if call[0] == 'generate_spec' and call[3].get('round') == 1]
    assert len(attempts) == 2, snapshot
    bad, good = attempts
    assert bad[1] == good[1] and (bad[2], good[2]) == (1, 2)
    assert bad[4] == good[4] and bad[5]['correction_scope'] == good[5]['correction_scope']
    assert good[5]['correction_scope']['binding']['accepted'] == base
    assert 'Clarify the bootstrap source observation.' in json.dumps(good[3])
    job = next(j for j in snapshot['jobs'] if j['id'] == good[1])
    candidate = job['outcome']['head']
    fas = json.loads(git(repo, 'show', f'{candidate}:.mentat/fas.json'))
    assert foreign_id not in {row['id'] for row in fas['requirements']}
    assert next(row for row in fas['surfaces'] if row['path'] == 'src.py')['note'] == 'Clarified review observation'
    fresh = [call for call in calls if call[0] == 'review_spec'
             and call[3]['proposal']['head'] == candidate]
    assert len(fresh) == 2 and len({call[1] for call in fresh}) == 2
    assert all(call[3]['proposal']['parent'] == base for call in fresh)
    admitted = json.loads(git(repo, 'show', f"{bad[4]}:.mentat/fas.json"))
    scope = good[5]['correction_scope']
    assert set(scope['records']['requirements']) == {row['id'] for row in admitted['requirements']}
    assert set(scope['records']['surfaces']) == {row['path'] for row in admitted['surfaces']}
    assert foreign_id not in scope['records']['requirements']

    # A capacity recovery can target the original absent-FAS checkpoint, but
    # it still carries the exact scope derived from the real durable review.
    from test_autonomy import GitHub, Worker

    from mentat.autonomy import Coordinator, _git_paths, current_tool_identity, digest, inventory
    from mentat.autonomy_spec_scope import authenticate
    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'state' / 'state.db')
    controller = Coordinator(repo, store, Agent(), Worker(), GitHub())
    controller.session_id = snapshot['session']['id']
    controller.authority_digest = digest(json.loads((store.path.parent / 'policy.json').read_text()))
    controller.tool_identity = current_tool_identity()
    recovery = {**job['payload'], 'target_head': base}
    recovery_id = store.enqueue_job(controller.session_id, 'scl', 'implementer', 'bootstrap-recovery',
                                    'bootstrap-recovery-input', recovery)
    persisted = next(j for j in Store(store.path).snapshot(controller.session_id)['jobs'] if j['id'] == recovery_id)
    assert authenticate(controller, persisted, inventory, _git_paths) == scope


@pytest.mark.parametrize('kind', ['absent', 'malformed', 'incomplete', 'symlink', 'directory', 'unreadable'])
def test_bootstrap_endpoint_accepts_only_verified_absence(repo, kind, monkeypatch):
    from types import SimpleNamespace

    from mentat import gitio
    from mentat.autonomy import inventory
    from mentat.autonomy_spec_scope import _endpoint

    fas = repo / '.mentat/fas.json'
    if kind == 'malformed':
        fas.write_text('{not json')
    elif kind in {'incomplete', 'unreadable'}:
        fas.write_text('{}')
    elif kind == 'symlink':
        fas.symlink_to('workspace.json')
    elif kind == 'directory':
        fas.mkdir()
        (fas / 'entry').write_text('not an absent file')
    if kind != 'absent':
        git(repo, 'add', '-A')
        git(repo, 'commit', '-qm', kind)
    revision = git(repo, 'rev-parse', 'HEAD')
    controller = SimpleNamespace(repo=repo)
    if kind == 'absent':
        assert _endpoint(controller, revision, inventory, allow_absent=True) == {
            'schema_version': 1, 'requirements': [], 'claims': [], 'surfaces': []}
        with pytest.raises(ValueError):
            _endpoint(controller, revision, inventory)
    else:
        if kind == 'unreadable':
            original = gitio.read_file
            def unreadable(root, rev, path):
                if path == '.mentat/fas.json':
                    raise OSError('fixture read denied')
                return original(root, rev, path)
            monkeypatch.setattr(gitio, 'read_file', unreadable)
        with pytest.raises((ValueError, OSError)):
            _endpoint(controller, revision, inventory, allow_absent=True)


def test_bootstrap_absence_rejects_recursive_listing_omission(repo, monkeypatch):
    from types import SimpleNamespace

    from mentat import gitio
    from mentat.autonomy import inventory
    from mentat.autonomy_spec_scope import _endpoint

    # The actual Git tree contains this file. Simulate a compromised/incomplete
    # inventory and recursive listing, while retaining the authentic top listing.
    (repo / '.mentat/fas.json').write_text('{}')
    git(repo, 'add', '.mentat/fas.json')
    git(repo, 'commit', '-qm', 'tracked FAS')
    revision = git(repo, 'rev-parse', 'HEAD')
    assert git(repo, 'show', f'{revision}:.mentat/fas.json') == '{}'
    original_listing = gitio.ls_tree
    omitted = []
    def incomplete_listing(root, rev, flags, scopes, deadline):
        for row in original_listing(root, rev, flags, scopes, deadline):
            if flags == '-rzt' and row.endswith(b'\t.mentat/fas.json'):
                omitted.append(row)
                continue
            yield row
    def incomplete_inventory(root, rev):
        return [item for item in inventory(root, rev) if item['path'] != '.mentat/fas.json']
    monkeypatch.setattr(gitio, 'ls_tree', incomplete_listing)
    with pytest.raises(ValueError):
        _endpoint(SimpleNamespace(repo=repo), revision, incomplete_inventory, allow_absent=True)
    assert len(omitted) == 1


def test_source_cycle_fresh_acceptance_ends_scope_for_remaining_frontier(tmp_path, monkeypatch):
    from test_autonomy_source_corrections import (
        test_source_revision_routes_to_current_head_specification_correction as source_cycle,
    )

    from mentat.autonomy_state import Store

    # This existing fixture exercises the real source finalization cycle, its
    # durable feedback, SCL revise, correction and independent reviewers.
    source_cycle(tmp_path, monkeypatch, False, 'first', True, True, 'accept')
    store = Store(tmp_path / 'state/state.db')
    snapshot = store.snapshot()
    repo_path = tmp_path / 'repo'
    scoped = [j for j in snapshot['jobs'] if j['payload'].get('kind') == 'generate_spec'
              and j['payload'].get('source_cycle_id') and j['payload'].get('correction_scope')]
    assert scoped, snapshot
    scope = scoped[0]['payload']['correction_scope']
    scoped_heads = {(j.get('outcome') or {}).get('head') for j in scoped}
    continuations = [j for j in snapshot['jobs'] if j['payload'].get('kind') == 'generate_spec'
                     and j['payload'].get('source_cycle_id') == scoped[0]['payload']['source_cycle_id']
                     and 'src.py' in j['payload'].get('targetfiles', [])
                     and 'correction_scope' not in j['payload']
                     and j['payload'].get('target_head') in scoped_heads
                     and (j.get('outcome') or {}).get('head')
                     and (j.get('outcome') or {}).get('head') != j['payload'].get('target_head')]
    for job in continuations:
        head = job['payload']['target_head']
        decisions = [d for d in snapshot['decisions'] if d.get('review_kind') == 'review_spec'
                     and d.get('head') == head and d.get('decision') == 'accept']
        if len({d['reviewer_id'] for d in decisions}) != 2 or len({d['review_cohort'] for d in decisions}) != 1:
            continue
        before = json.loads(git(repo_path, 'show', f'{head}:.mentat/fas.json'))
        after = json.loads(git(repo_path, 'show', f"{job['outcome']['head']}:.mentat/fas.json"))
        prior = {row['path']: row for row in before['surfaces']}
        changed = {row['path'] for row in after['surfaces'] if prior.get(row['path']) != row}
        if changed - set(scope['records']['surfaces']):
            assert job['payload']['feedback'] == scoped[0]['payload']['feedback']
            assert job['payload']['source_cycle_id'] == scoped[0]['payload']['source_cycle_id']
            break
    else:
        pytest.fail('no source-cycle frontier independently accepted the scoped correction and changed an unrelated surface')


def test_correction_scope_reserves_context_before_optional_source(repo, tmp_path):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    paths = []
    for index in range(10):
        path = f'optional-{index}.py'
        (repo / path).write_text('# ' + 'x' * 55_000)
        paths.append(path)
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'optional sources')
    head = git(repo, 'rev-parse', 'HEAD')
    coordinator = Coordinator(repo, Store(tmp_path / 'budget/state.db'), None, None, None)
    scope = {'id': 'scope-id', 'binding': {'accepted': head},
             'records': {'requirements': [], 'claims': [],
                         'surfaces': [f'path-{index:04}-' + 'x' * 100 for index in range(2200)]},
             'files': []}
    plain_job = {'loop': 'scl', 'payload': {'kind': 'generate_spec', 'target_head': head}}
    scoped_job = {'loop': 'scl', 'payload': {**plain_job['payload'], 'correction_scope': scope}}
    _, _, plain_included, _ = coordinator._source_context(head, plain_job)
    text, omitted, included, _ = coordinator._source_context(head, scoped_job)
    data = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert data['correction_scope'] == scope
    assert len(json.dumps(data, ensure_ascii=True)) <= 500_000
    assert len(set(paths) & included) < len(set(paths) & plain_included)
    assert set(paths) & (plain_included - included) <= omitted
    assert scoped_job['payload']['correction_scope'] == scope


def test_oversized_mandatory_scope_stops_before_agent_call(repo, tmp_path, monkeypatch):
    import copy
    import time

    from test_autonomy import GitHub, Worker

    from mentat import autonomy_spec_scope
    from mentat.autonomy import Coordinator, ReviewContextCapacity, default_config
    from mentat.autonomy_state import Store

    class MustNotRun(Agent):
        def run(self, *args, **kwargs):
            pytest.fail('agent must not receive an over-budget mandatory scope')

    head = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'overflow/state.db')
    coordinator = Coordinator(repo, store, MustNotRun(), Worker(), GitHub())
    coordinator.config = default_config(repo)
    coordinator.session_id = store.new_session(str(repo), head, head, coordinator.config,
                                               now=time.time(), deadline=time.time() + 100)
    scope = {'id': 'scope-id', 'binding': {'accepted': head},
             'records': {'requirements': [], 'claims': [], 'surfaces': ['x' * 500_000]}, 'files': []}
    payload = {'kind': 'generate_spec', 'target_head': head, 'review_base': head,
               'correction_scope': scope}
    job = {'id': 'scope-overflow', 'attempt': 1, 'item_id': 'cas.inventory', 'loop': 'scl',
           'payload': payload}
    saved = copy.deepcopy(job)
    monkeypatch.setattr(autonomy_spec_scope, 'authenticate', lambda *args: scope)
    with pytest.raises(ReviewContextCapacity):
        coordinator._execute(job, time.time() + 60)
    assert job == saved


@pytest.mark.parametrize('base_has_fas', [False, True])
@pytest.mark.parametrize('operation', ['write', 'patch_fas'])
def test_initial_manual_feature_scope_authorizes_only_reviewed_spec_delta(repo, tmp_path, base_has_fas, operation):
    import uuid

    from test_autonomy import _valid_publication_spec

    from mentat.autonomy import AREAS

    if base_has_fas:
        _valid_publication_spec(repo)
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('def value(): return 1  # manual feature\n')
    spec = repo / 'openspec/specs/app/spec.md'
    if base_has_fas:
        fas = json.loads((repo / '.mentat/fas.json').read_text())
        req_id = fas['requirements'][0]['id']
        next(row for row in fas['surfaces'] if row['path'] == 'src.py').update(
            status='mapped', requirement_ids=[req_id])
        spec.write_text(spec.read_text() + '\nManual feature requirement.\n')
    else:
        spec.parent.mkdir(parents=True)
        requirements = [{'id': str(uuid.uuid4()), 'area': area, 'origin': 'uncertain',
                         'statement': 'Manual feature intent', 'spec_path': str(spec.relative_to(repo))}
                        for area in AREAS]
        spec.write_text('\n'.join(row['id'] for row in requirements) + '\nManual feature requirement.\n')
        req_id = requirements[0]['id']
        fas = {'schema_version': 1, 'requirements': requirements, 'claims': [], 'surfaces': [
            {'path': path, 'status': 'mapped' if path == 'src.py' else 'unexamined',
             'requirement_ids': [req_id] if path == 'src.py' else []}
            for path in ('src.py', '.mentat/workspace.json', '.mentat/fas.json', 'openspec/specs/app/spec.md')]}
    (repo / '.mentat/fas.json').write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'manual feature and specification')
    feature = git(repo, 'rev-parse', 'HEAD')
    calls = []

    class FeatureAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
            calls.append((kind, job['id'], job['attempt'], payload, context, git(repo, 'rev-parse', 'HEAD')))
            if kind == 'generate_spec':
                if payload.get('round') != 1:
                    return response(job)
                data = (repo / '.mentat/fas.json').read_bytes()
                current = json.loads(data)
                row = next(row for row in current['surfaces'] if row['path'] == 'src.py')
                original = dict(row)
                row['note'] = 'Clarified observed feature.'
                if operation == 'write':
                    change = {'path': '.mentat/fas.json', 'operation': 'write', 'content': json.dumps(current)}
                else:
                    patch = {'schema_version': 1, 'expected_sha256': hashlib.sha256(data).hexdigest(),
                             'edits': [{'collection': 'surfaces', 'key': 'src.py',
                                        'expected_digest': record_digest(original), 'record': row}]}
                    change = {'path': '.mentat/fas.json', 'operation': 'patch_fas', 'content': json.dumps(patch)}
                changes = [change, {'path': 'openspec/specs/app/spec.md', 'operation': 'write',
                                    'content': (repo / 'openspec/specs/app/spec.md').read_text() + '\nClarified.\n'}]
                if job['attempt'] == 1:
                    changes.append({'path': 'src.py', 'operation': 'write', 'content': 'value = 99\n'})
                return response(job, changes)
            if kind == 'generate_verification':
                return response(job)
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'review_spec' and payload['proposal'].get('round', 0) == 0 and job['item_id'].endswith('.1'):
                result['result']['decision'] = 'revise'
                result['result']['findings'] = [{'summary': 'Clarify the reviewed feature.'}]
            return result

    snapshot, _, _ = run(repo, tmp_path, agent=FeatureAgent())
    initial = [call for call in calls if call[0] == 'generate_spec' and not call[3].get('round')]
    retry = [call for call in calls if call[0] == 'generate_spec' and call[3].get('round') == 1]
    assert len(initial) == 1 and initial[0][5] == feature
    assert len(retry) == 2, snapshot
    assert retry[0][1] == retry[1][1] and [entry[2] for entry in retry] == [1, 2]
    assert retry[0][5] == retry[1][5] == feature
    assert retry[0][4]['correction_scope'] == retry[1][4]['correction_scope']
    correction = next(job for job in snapshot['jobs'] if job['id'] == retry[1][1])
    corrected = correction['outcome']['head']
    assert corrected != feature
    assert git(repo, 'show', f'{corrected}:src.py') == 'def value(): return 1  # manual feature'
    assert 'Clarified.' in git(repo, 'show', f'{corrected}:openspec/specs/app/spec.md')
    corrected_fas = json.loads(git(repo, 'show', f'{corrected}:.mentat/fas.json'))
    assert next(row for row in corrected_fas['surfaces'] if row['path'] == 'src.py')['note'] == 'Clarified observed feature.'
    reviews = [call for call in calls if call[0] == 'review_spec' and call[3]['proposal']['head'] == corrected]
    assert len(reviews) == 2 and len({call[1] for call in reviews}) == 2
    assert all(call[3]['proposal']['parent'] == base for call in reviews)

    if base_has_fas or operation != 'write':
        return

    import sqlite3
    import time

    from test_autonomy import GitHub, Worker

    from mentat.autonomy import Coordinator, _git_paths, current_tool_identity, digest, inventory
    from mentat.autonomy_spec_scope import authenticate
    from mentat.autonomy_spec_scope import digest as scope_digest
    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'state/state.db')
    sid = snapshot['session']['id']
    controller = Coordinator(repo, store, Agent(), Worker(), GitHub())
    controller.session_id = sid
    controller.authority_digest = digest(json.loads((store.path.parent / 'policy.json').read_text()))
    controller.tool_identity = current_tool_identity()
    original_scope = correction['payload']['correction_scope']
    assert authenticate(controller, correction, inventory, _git_paths) == original_scope
    store.append_event(sid, 'fas_reviewed', {'head': corrected})
    controller.store = Store(store.path)
    assert authenticate(controller, correction, inventory, _git_paths) == original_scope

    # The historical proof must remain stable, while a fresh write based on
    # that old input cannot supersede the later accepted correction.
    class StaleAgent(Agent):
        def run(self, *args, **kwargs):
            pytest.fail('stale correction must not reach the agent')

    controller.agents = StaleAgent()
    controller.config = json.loads((repo / '.mentat/workspace.json').read_text())
    stale_id = store.enqueue_job(sid, 'scl', 'implementer', 'stale-manual-correction',
                                 'stale-manual-input', correction['payload'])
    stale = next(job for job in store.snapshot(sid)['jobs'] if job['id'] == stale_id)
    stale['attempt'] = 1
    with pytest.raises(ValueError):
        controller._execute(stale, time.time() + 60)

    first_reviews = [job for job in snapshot['jobs'] if job['payload'].get('kind') == 'review_spec'
                     and job['payload']['proposal'].get('head') == feature
                     and job['payload']['proposal'].get('round', 0) == 0]
    assert len(first_reviews) == 2
    first_ids = {job['id'] for job in first_reviews}
    events = [event for event in snapshot['events'] if event['kind'] == 'job_enqueued'
              and event['payload'].get('job_id') in first_ids]
    assert len(events) == 2
    events.sort(key=lambda event: event['seq'])

    # Mutate disposable DB copies only, leaving the real review decisions and
    # jobs in place. A missing, changed, or raced scheduling proof must fail.
    for variant in ('missing', 'changed', 'between'):
        copied = tmp_path / f'tampered-{variant}.db'
        with sqlite3.connect(store.path) as original, sqlite3.connect(copied) as duplicate:
            original.backup(duplicate)
        with sqlite3.connect(copied) as db:
            if variant == 'missing':
                db.execute('DELETE FROM events WHERE seq = ?', (events[0]['seq'],))
            elif variant == 'changed':
                altered = json.loads(json.dumps(events[0]['payload']))
                altered['payload']['review_cohort'] = 'unrelated-cohort'
                db.execute('UPDATE events SET payload = ? WHERE seq = ?',
                           (json.dumps(altered), events[0]['seq']))
            else:
                second = events[1]['seq']
                db.execute('UPDATE events SET seq = seq + 1000000 WHERE seq >= ?', (second,))
                db.execute('INSERT INTO events(seq, session_id, kind, payload) VALUES (?, ?, ?, ?)',
                           (second, sid, 'fas_reviewed', json.dumps({'head': feature})))
        controller.store = Store(copied)
        with pytest.raises(ValueError):
            authenticate(controller, correction, inventory, _git_paths)

    # A model or caller cannot rehash any altered binding into valid authority.
    controller.store = store
    for key, value in original_scope['binding'].items():
        forged = json.loads(json.dumps(correction['payload']))
        binding = forged['correction_scope']['binding']
        if isinstance(value, str):
            binding[key] = value + '-forged'
        elif type(value) is int:
            binding[key] = value + 1
        elif isinstance(value, list):
            binding[key] = [*value, 'forged']
        else:
            pytest.fail(f'unexpected correction binding type for {key}')
        scope = forged['correction_scope']
        scope['id'] = scope_digest({field: item for field, item in scope.items() if field != 'id'})
        jid = store.enqueue_job(sid, 'scl', 'implementer', 'forged-manual-' + key,
                                'forged-input-' + key, forged)
        forged_job = next(job for job in store.snapshot(sid)['jobs'] if job['id'] == jid)
        with pytest.raises(ValueError):
            authenticate(controller, forged_job, inventory, _git_paths)


def test_initial_manual_feature_does_not_treat_invalid_present_baseline_as_empty(repo, tmp_path):
    from test_autonomy import _valid_publication_spec

    _valid_publication_spec(repo)
    path = repo / '.mentat/fas.json'
    valid = path.read_text()
    path.write_text('{invalid')
    git(repo, 'commit', '-qam', 'invalid existing baseline')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', base)
    path.write_text(valid)
    (repo / 'src.py').write_text('def value(): return 1  # feature\n')
    spec = repo / 'openspec/specs/app/spec.md'
    spec.write_text(spec.read_text() + '\nFeature specification\n')
    git(repo, 'commit', '-qam', 'feature fixes FAS but does not validate old baseline')
    calls = []

    class InitialAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            calls.append((payload['kind'], payload.get('round', 0)))
            if payload['kind'] in {'generate_spec', 'generate_verification'}:
                return response(job)
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if payload['kind'] == 'review_spec' and job['item_id'].endswith('.1'):
                result['result']['decision'] = 'revise'
                result['result']['findings'] = [{'summary': 'Clarify feature.'}]
            return result

    snapshot, _, github = run(repo, tmp_path, agent=InitialAgent())
    assert not any(kind == 'generate_spec' and round_number > 0 for kind, round_number in calls)
    assert not github.published
    assert snapshot['session']['status'] != 'completed'


def test_previously_accepted_checkpoint_noop_cannot_widen_to_unrelated_spec(repo, tmp_path):
    requirements, paths = _review_capacity_repo(repo)
    for path in paths:
        (repo / path).write_text('# ' + 'x' * 55_000 + '\n')
    git(repo, 'commit', '-qam', 'source exceeds one context')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    calls = []

    class PriorAcceptedAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            calls.append((kind, job['id'], job['attempt'], payload, git(repo, 'rev-parse', 'HEAD')))
            if kind == 'generate_spec':
                if payload.get('round', 0) > 0:
                    doc = repo / 'openspec/specs/application/spec.md'
                    return response(job, [{'path': str(doc.relative_to(repo)), 'operation': 'write',
                                           'content': doc.read_text() + '\nUnrelated after approval.\n'}])
                if not payload.get('targetfiles'):
                    fas = json.loads((repo / '.mentat/fas.json').read_text())
                    next(row for row in fas['surfaces'] if row['path'] == paths[0]).update(
                        status='mapped', requirement_ids=[requirements[0]['id']])
                    return response(job, [{'path': '.mentat/fas.json', 'operation': 'write',
                                           'content': json.dumps(fas)}])
                return response(job)
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if (kind == 'review_spec' and payload.get('targetfiles')
                    and payload['proposal'].get('round', 0) == 0 and job['item_id'].endswith('.1')):
                result['result']['decision'] = 'revise'
                result['result']['findings'] = [{'summary': 'Clarify unrelated specification.'}]
            return result

    snapshot, _, _ = run(repo, tmp_path, agent=PriorAcceptedAgent())
    revisions = [call for call in calls if call[0] == 'generate_spec' and call[3].get('round', 0) > 0]
    assert len(revisions) == 3, snapshot
    assert len({call[1] for call in revisions}) == 1 and [call[2] for call in revisions] == [1, 2, 3]
    head = revisions[0][4]
    assert all(call[4] == head for call in revisions)
    assert 'Unrelated after approval.' not in git(repo, 'show', f'{head}:openspec/specs/application/spec.md')
    event = next(event for event in snapshot['events'] if event['kind'] == 'fas_reviewed'
                 and event['payload'].get('head') == head)
    reviewers = [job for job in snapshot['jobs'] if job['payload'].get('kind') == 'review_spec'
                 and job['payload'].get('targetfiles') and job['payload']['proposal']['head'] == head]
    enqueues = [event2 for event2 in snapshot['events'] if event2['kind'] == 'job_enqueued'
                and event2['payload'].get('job_id') in {job['id'] for job in reviewers}]
    assert len(reviewers) == len(enqueues) == 2
    assert event['seq'] < min(item['seq'] for item in enqueues)


def test_newer_genuinely_reviewed_checkpoint_fences_inflight_correction_acceptance(repo, tmp_path, monkeypatch):
    import time
    import uuid

    from mentat.autonomy import AREAS, Coordinator, _git_paths, inventory
    from mentat.autonomy_spec_scope import authenticate
    from mentat.autonomy_state import Store

    # The paused agent drives competing jobs synchronously; one worker keeps
    # the coordinator executor from claiming those same jobs concurrently.
    config_path = repo / '.mentat/workspace.json'
    config = json.loads(config_path.read_text())
    config['concurrency'] = 1
    config_path.write_text(json.dumps(config))
    git(repo, 'commit', '-qam', 'isolate race fixture worker')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    base = git(repo, 'rev-parse', 'HEAD')
    spec = repo / 'openspec/specs/application/spec.md'
    spec.parent.mkdir(parents=True)
    requirements = [{'id': str(uuid.uuid4()), 'area': area, 'origin': 'uncertain',
                     'statement': 'Feature intent', 'spec_path': 'openspec/specs/application/spec.md'}
                    for area in AREAS]
    spec.write_text('\n'.join(row['id'] for row in requirements))
    fas = {'schema_version': 1, 'requirements': requirements, 'claims': [], 'surfaces': [
        {'path': path, 'status': 'unexamined', 'requirement_ids': []} for path in (
            'src.py', '.mentat/workspace.json', '.mentat/fas.json', 'openspec/specs/application/spec.md')]}
    (repo / '.mentat/fas.json').write_text(json.dumps(fas))
    (repo / 'src.py').write_text('def value(): return 1  # manual feature\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'initial manual feature')
    feature = git(repo, 'rev-parse', 'HEAD')
    extra_id = str(uuid.uuid4())
    state = {'coordinator': None, 'newer': None, 'injected': False}
    calls = []
    original_execute = Coordinator._execute

    def execute_and_finish(controller, expected_kind):
        claimed = controller.store.claim_job(controller.session_id, 'race-fixture', now=time.time(), lease_seconds=120)
        assert claimed and claimed['payload']['kind'] == expected_kind
        outcome = original_execute(controller, claimed, time.time() + 120)
        assert outcome['status'] == 'succeeded'
        controller.store.finish_job(claimed['id'], claimed['lease_token'], outcome, now=time.time())
        if expected_kind == 'generate_spec':
            state['newer'] = outcome['head']
        controller._drain_transitions()
        return claimed, outcome

    class RaceAgent(Agent):
        def run(self, worktree, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            calls.append((kind, job['id'], payload, git(worktree, 'rev-parse', 'HEAD')))
            if kind == 'generate_spec' and payload.get('race_competitor'):
                current = json.loads((worktree / '.mentat/fas.json').read_text())
                current['requirements'].append({**current['requirements'][0], 'id': extra_id,
                                                'statement': 'Independently reviewed later intent'})
                doc = worktree / 'openspec/specs/application/spec.md'
                return response(job, [
                    {'path': '.mentat/fas.json', 'operation': 'write', 'content': json.dumps(current)},
                    {'path': 'openspec/specs/application/spec.md', 'operation': 'write',
                     'content': doc.read_text() + '\n' + extra_id + '\n'},
                ])
            if kind == 'generate_spec' and payload.get('round') == 1 and not state['injected']:
                state['injected'] = True
                controller = state['coordinator']
                assert controller is not None
                controller._enqueue('scl', 'implementer', 'race.competitor', {
                    'kind': 'generate_spec', 'target_head': feature, 'accepted_spec_base': feature,
                    'review_base': base, 'surfaces': inventory(repo, feature), 'race_competitor': True})
                execute_and_finish(controller, 'generate_spec')
                assert state['newer'] and state['newer'] != feature
                for _ in range(2):
                    review, _ = execute_and_finish(controller, 'review_spec')
                    assert review['payload']['proposal']['head'] == state['newer']
                saved = controller.store.snapshot(controller.session_id)
                assert any(event['kind'] == 'fas_reviewed' and event['payload']['head'] == state['newer']
                           for event in saved['events'])
                doc = worktree / 'openspec/specs/application/spec.md'
                return response(job, [{'path': 'openspec/specs/application/spec.md', 'operation': 'write',
                                       'content': doc.read_text() + '\nStale clarification.\n'}])
            if kind in {'generate_spec', 'generate_verification'}:
                return response(job)
            result = super().run(worktree, job, output, timeout_seconds=timeout_seconds)
            if (kind == 'review_spec' and payload['proposal']['head'] == feature
                    and payload['proposal'].get('round', 0) == 0 and job['item_id'].endswith('.1')):
                result['result']['decision'] = 'revise'
                result['result']['findings'] = [{'summary': 'Clarify the feature.'}]
            return result

    def observe_controller(controller, job, deadline):
        state['coordinator'] = controller
        return original_execute(controller, job, deadline)

    monkeypatch.setattr(Coordinator, '_execute', observe_controller)
    # The shared test harness sets a GitHub adapter; its returned
    # instance lets us check the actual publication targets.
    snapshot, _, github = run(repo, tmp_path, agent=RaceAgent(), concurrency=1)
    assert state['injected'] and state['newer']
    correction = next(job for job in snapshot['jobs'] if job['payload'].get('kind') == 'generate_spec'
                      and job['payload'].get('correction_scope') and (job.get('outcome') or {}).get('head'))
    stale = correction['outcome']['head']
    assert stale not in {feature, state['newer']}
    real_reviews = [job for job in snapshot['jobs'] if job['payload'].get('kind') == 'review_spec'
                    and job['payload']['proposal']['head'] == stale and job['status'] == 'succeeded']
    assert len(real_reviews) == 2
    assert all(job['outcome']['result']['decision'] == 'accept' for job in real_reviews)
    assert len({job['outcome']['author_id'] for job in real_reviews}) == 2
    newer_event = next(event for event in snapshot['events'] if event['kind'] == 'fas_reviewed'
                       and event['payload']['head'] == state['newer'])
    fresh_enqueues = [event for event in snapshot['events'] if event['kind'] == 'job_enqueued'
                      and event['payload'].get('job_id') in {job['id'] for job in real_reviews}]
    assert len(fresh_enqueues) == 2 and newer_event['seq'] < min(event['seq'] for event in fresh_enqueues)
    assert not any(event['kind'] == 'fas_reviewed' and event['payload']['head'] == stale
                   for event in snapshot['events'])
    assert any(row.get('id') == extra_id for row in snapshot['requirements'])
    assert not any(job['payload'].get('kind') == 'generate_verification'
                   and job['payload'].get('target_head') == stale for job in snapshot['jobs'])
    assert stale not in github.published
    assert any('does not retain a newer accepted specification' in row.get('summary', '')
               for row in snapshot['findings'])
    controller = state['coordinator']
    controller.store = Store(tmp_path / 'state/state.db')
    assert authenticate(controller, correction, inventory, _git_paths) == correction['payload']['correction_scope']
