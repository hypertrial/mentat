"""Source review revisions must route to current-head specification correction."""

import json
import threading
from copy import deepcopy
from pathlib import Path

import pytest
from test_autonomy import Agent, _valid_publication_spec, git, run
from test_autonomy import repo as repo_fixture


@pytest.mark.parametrize('peer_defers', [False, True])
@pytest.mark.parametrize('review_order', ['first', 'last'])
def test_source_revision_routes_to_current_head_specification_correction(
    tmp_path, monkeypatch, peer_defers, review_order
):
    from mentat import autonomy_runtime

    repo = repo_fixture.__wrapped__(tmp_path)
    _valid_publication_spec(repo)
    fas_path = repo / '.mentat/fas.json'
    fas = json.loads(fas_path.read_text())
    requirement = fas['requirements'][0]['id']
    for row in fas['surfaces']:
        if row['path'] == 'src.py':
            row.update(status='mapped', requirement_ids=[requirement], note='Overbroad source assertion.')
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline mapping')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', base)
    spec = repo / 'openspec/specs/app/spec.md'
    spec.write_text(spec.read_text() + '\nReview this source mapping.\n')
    git(repo, 'commit', '-qam', 'candidate CAS')
    lock = threading.Lock()
    local = threading.local()
    source_code_returned = threading.Event()
    state = {'source_revised': False, 'corrected': False, 'drain': None, 'code_job': None}
    calls = []
    original_run = autonomy_runtime._run
    features = ('shell_tool', 'unified_exec', 'apps', 'browser_use', 'computer_use',
                'multi_agent', 'code_mode', 'code_mode_host')

    def fake_run(argv, **kwargs):
        if argv[:3] == ['codex', 'exec', '--help']:
            return '--ignore-user-config --disable'
        if argv[:3] == ['codex', 'features', 'list']:
            return '\n'.join(f'{feature} stable true' for feature in features)
        return original_run(argv, **kwargs)

    def fake_execute(self, argv, **kwargs):
        job = local.job
        payload = job['payload']
        prompt = argv[-1]
        assert prompt == payload['prompt']
        schema = json.loads(Path(argv[argv.index('--output-schema') + 1]).read_text())
        assert 'summary' in schema['required']
        source_scope = payload['source_batch']
        has_source = any(row['path'] == 'src.py' for row in source_scope['obligations'])
        target_role = 'code-reviewer' if review_order == 'first' else 'data-reviewer'
        if has_source and job['role'] == 'data-reviewer' and not state['corrected']:
            assert source_code_returned.wait(timeout=15)
            if review_order == 'last':
                import time

                from mentat.autonomy_state import Store

                store = Store(tmp_path / 'state/state.db')
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        if store.transition_status(state['code_job']) == 'done':
                            break
                    except ValueError:
                        pass
                    time.sleep(0.01)
                else:
                    raise AssertionError('first reviewer did not advance')
        with lock:
            revise = has_source and job['role'] == target_role and not state['source_revised']
            if revise:
                state['source_revised'] = True
            defer = has_source and job['role'] != target_role and peer_defers and not state['corrected']
        detail = {'findings': 'The source note overstates the observed behavior.',
                  'unresolved_dependencies': ['Caller context is unavailable.'] if defer else []}
        if 'spec_corrections' in prompt:
            detail.update(schema_version=2, spec_corrections=[{
                'collection': 'surfaces', 'key': 'src.py',
                'reason': 'Narrow the note to the observed return value.'}] if revise else [])
        answer = {'schema_version': 1, 'decision': 'revise' if revise else ('defer' if defer else 'accept'),
                  'summary': json.dumps(detail), 'requirements': [], 'claims': [],
                  'findings': [{'id': 'source-finding', 'summary': 'Source returns 1.',
                                'requirement_ids': [requirement], 'severity': 'medium'}] if revise else [],
                  'verification_requests': [], 'defects': [], 'changes': []}
        Path(argv[argv.index('--output-last-message') + 1]).write_text(json.dumps(answer))
        if has_source and job['role'] == 'code-reviewer':
            state['code_job'] = job['id']
            source_code_returned.set()
        return json.dumps({'type': 'thread.started', 'thread_id': job['id']}) + '\n'

    monkeypatch.setattr(autonomy_runtime, '_run', fake_run)
    monkeypatch.setattr(autonomy_runtime.CodexAgent, '_execute', fake_execute)

    class SourceCorrectionAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            with lock:
                calls.append((kind, deepcopy(payload), git(repo, 'rev-parse', 'HEAD')))
                revised = state['source_revised']
                corrected = state['corrected']
            if kind == 'review_final_source':
                local.job = job
                return autonomy_runtime.CodexAgent().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'generate_spec':
                if revised:
                    from mentat.autonomy_state import Store

                    store = Store(tmp_path / 'state/state.db')
                    live_snapshot = store.snapshot()
                    old_jobs = [row for row in live_snapshot['jobs']
                                if row['payload'].get('kind') == 'review_final_source'
                                and row['payload'].get('target_head') == payload['target_head']]
                    with lock:
                        state['drain'] = [(row['id'], row['status'], store.transition_status(row['id']))
                                          for row in old_jobs]
                    path = repo / '.mentat/fas.json'
                    current = json.loads(path.read_text())
                    next(row for row in current['surfaces'] if row['path'] == 'src.py')['note'] = (
                        'The selected function returns 1.')
                    path.write_text(json.dumps(current))
                    with lock:
                        state['corrected'] = True
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'Specification reviewed', 'findings': [],
                    'verification_requests': []}}
            if kind == 'generate_verification' and revised:
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept' if corrected else 'defer', 'summary': 'Verification unchanged',
                    'findings': [], 'verification_requests': []}}
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=SourceCorrectionAgent())
    source = [(payload, head) for kind, payload, head in calls if kind == 'review_final_source']
    assert source, snapshot
    first_head = source[0][1]
    ivl_retries = [(payload, head) for kind, payload, head in calls
                   if kind == 'generate_verification' and payload.get('round', 0) > 0]
    assert not ivl_retries, ('source revise incorrectly routed to IVL', ivl_retries)
    if peer_defers:
        assert snapshot['session']['status'] == 'blocked'
        assert any('source correction unresolved' in row.get('summary', '') for row in snapshot['findings'])
        assert any(event['kind'] == 'source_correction_checkpoint'
                   and 'Caller context is unavailable.' in json.dumps(event['payload'])
                   for event in snapshot['events'])
        assert not github.published and not github.merged
        assert not any(kind == 'generate_spec' and payload.get('source_cycle_round', 0) > 0
                       for kind, payload, _ in calls)
        return
    corrections = [(payload, head) for kind, payload, head in calls
                   if kind == 'generate_spec' and payload.get('source_cycle_round', 0) > 0]
    assert len(corrections) == 1, snapshot
    payload, correction_head = corrections[0]
    assert correction_head == payload['target_head'] == payload['accepted_spec_base'] == first_head
    assert git(repo, 'show', f'{correction_head}:verification/check.txt') == 'check'
    assert 'Overbroad source assertion.' in git(repo, 'show', f'{correction_head}:.mentat/fas.json')
    feedback = json.dumps(payload['feedback'])
    assert 'Narrow the note to the observed return value.' in feedback
    assert 'The source note overstates the observed behavior.' in feedback
    assert 'Source returns 1.' in feedback and 'source-finding' in feedback
    expected_batch_ids = source[0][0]['source_batch_ids']
    assert len(state['drain']) == 2 * len(expected_batch_ids)
    assert all(status == 'succeeded' and transition == 'done' for _, status, transition in state['drain'])
    assert github.published, snapshot
    assert first_head not in github.published
    assert len(set(github.published)) == 1
    final_head = github.published[0]
    assert final_head != first_head
    later_source = [(payload, head) for payload, head in source if head == final_head]
    assert later_source
    batches = {payload['source_batch']['id'] for payload, _ in later_source}
    assert all(sum(p['source_batch']['id'] == batch for p, _ in later_source) == 2 for batch in batches)
    for kind in ('review_verification', 'review_final'):
        pair = [(payload, head) for stage, payload, head in calls if stage == kind and head == final_head]
        assert len(pair) == 2
        assert all(payload['check_summary']['head'] == final_head for payload, _ in pair)
    decisions = [row for row in snapshot['decisions'] if row.get('review_kind') == 'review_final_source'
                 and row.get('head') == final_head and row.get('status') == 'passed']
    assert len(decisions) == len(later_source)
    for decision in decisions:
        manifest = json.loads((tmp_path / 'state' / decision['input_manifest']).read_bytes())
        assert manifest['head'] == final_head
    assert git(repo, 'show', f'{final_head}:verification/check.txt') == 'check'
    final_fas = json.loads(git(repo, 'show', f'{final_head}:.mentat/fas.json'))
    assert next(row for row in final_fas['surfaces'] if row['path'] == 'src.py')['note'] == (
        'The selected function returns 1.')


def _source_review_scope():
    requirement = {'id': 'req-1', 'citations': []}
    foreign = {'id': 'req-other', 'citations': []}
    claim = {'id': 'claim-1', 'requirement_ids': ['req-1'], 'citations': []}
    surface = {'path': 'src.py', 'status': 'mapped', 'requirement_ids': ['req-1']}
    return {'obligations': [{'path': 'src.py', 'mode': 'current-source',
                             'base_surface': surface, 'current_surface': surface}],
            'records': {'base': {'requirements': [requirement, foreign], 'claims': [claim]},
                        'current': {'requirements': [requirement, foreign], 'claims': [claim]}}}


def _review_result(decision='revise', corrections=None, dependencies=None):
    detail = {'schema_version': 2, 'findings': 'Observed mapping is too broad.',
              'unresolved_dependencies': dependencies or [],
              'spec_corrections': corrections if corrections is not None else [
                  {'collection': 'surfaces', 'key': 'src.py', 'reason': 'Narrow the observation.'}]}
    return {'decision': decision, 'summary': json.dumps(detail), 'findings': [],
            'requirements': [], 'claims': [], 'verification_requests': [], 'defects': [], 'changes': []}


@pytest.mark.parametrize(('collection', 'key'), [
    ('requirements', 'req-1'), ('claims', 'claim-1'), ('surfaces', 'src.py')])
def test_actionable_v2_correction_requires_existing_scoped_record(collection, key):
    from mentat.autonomy_source_corrections import validate_result

    target = {'collection': collection, 'key': key, 'reason': 'Revise the stated correspondence.'}
    result = _review_result(corrections=[target])
    assert validate_result(result, _source_review_scope(), {'src.py'})['spec_corrections'] == [target]


@pytest.mark.parametrize('fault', ['legacy', 'unknown_requirement', 'unknown_claim', 'unknown_surface',
                                   'foreign_requirement', 'unsafe_surface', 'empty_reason', 'duplicate',
                                   'empty_targets', 'unresolved', 'accept_targets', 'unsupported_defect',
                                   'oversized'])
def test_unsupported_or_ambiguous_source_correction_never_qualifies(fault):
    from mentat.autonomy_source_corrections import validate_result

    target = {'collection': 'surfaces', 'key': 'src.py', 'reason': 'Review scope.'}
    result = _review_result(corrections=[target])
    detail = json.loads(result['summary'])
    target = detail['spec_corrections'][0]
    if fault == 'legacy':
        detail = {'findings': 'Legacy review.', 'unresolved_dependencies': []}
    elif fault == 'unknown_requirement':
        target.update(collection='requirements', key='missing')
    elif fault == 'unknown_claim':
        target.update(collection='claims', key='missing')
    elif fault == 'unknown_surface':
        target['key'] = 'other.py'
    elif fault == 'foreign_requirement':
        target.update(collection='requirements', key='req-other')
    elif fault == 'unsafe_surface':
        target['key'] = '../outside.py'
    elif fault == 'empty_reason':
        target['reason'] = ' '
    elif fault == 'duplicate':
        detail['spec_corrections'].append(deepcopy(target))
    elif fault == 'empty_targets':
        detail['spec_corrections'] = []
    elif fault == 'unresolved':
        detail['unresolved_dependencies'] = ['Cannot inspect caller.']
    elif fault == 'accept_targets':
        result['decision'] = 'accept'
    elif fault == 'unsupported_defect':
        result['defects'] = [{'summary': 'Application defect'}]
    else:
        result['findings'] = [{'summary': 'x' * 4097}]
    result['summary'] = json.dumps(detail)
    with pytest.raises(ValueError):
        validate_result(result, _source_review_scope(), {'src.py', 'other.py'})


def _source_job_payload(head, plan, check, batch='batch'):
    return {'kind': 'review_final_source', 'target_head': head, 'source_cycle_id': 'cycle',
            'source_batch': {'id': batch, 'plan_id': plan}, 'check_key': check,
            'check_evidence_id': 'evidence', 'proposal': {'head': head, 'diff_digest': f'diff-{head}'}}


def _source_outcome_and_decision(job_id, head, check, reviewer, decision_id, result):
    manifest = f'agents/{job_id}/1/manifest.json'
    manifest_hash = 'a' * 64
    outcome = {'status': 'succeeded', 'author_id': reviewer, 'head': head,
               'diff_digest': f'diff-{head}', 'input_manifest': manifest,
               'input_manifest_sha256': manifest_hash, 'result': result}
    decision = {'id': decision_id, 'job_id': job_id, 'head': head, 'diff_digest': f'diff-{head}',
                'reviewer_id': reviewer, 'decision': result['decision'],
                'source_batch_id': 'batch', 'check_key': check, 'check_evidence_id': 'evidence',
                'input_manifest': manifest, 'input_manifest_sha256': manifest_hash,
                'summary': result['summary'], 'findings': result['findings']}
    return outcome, decision


def test_source_revision_fences_remote_effect_atomically(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'atomic/state.db')
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'head', {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'logical-final-item', 'head', 'base',
                               'plan', 'check', ['batch'], continuing=False)
    payload = _source_job_payload('head', 'plan', 'check')
    job_id = store.enqueue_job(session, 'ivl', 'code-reviewer', 'source', 'input', payload)
    store.enqueue_job(session, 'ivl', 'data-reviewer', 'source-peer', 'peer-input', payload)
    job = store.claim_job(session, 'worker', now=2)
    assert job and job['id'] == job_id
    outcome, decision = _source_outcome_and_decision(
        job_id, 'head', 'check', 'reviewer', 'revision', _review_result())
    store.finish_job(job_id, job['lease_token'], outcome, now=3)
    start = threading.Barrier(2)

    def revise():
        start.wait()
        try:
            store.record_source_decision(session, 'cycle', decision)
            return 'revision'
        except ValueError:
            return None

    def publish():
        start.wait()
        try:
            store.begin_remote_effect(session, 'publication_started', {'head': 'head'})
            return 'publication'
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(revise), pool.submit(publish))]
    assert sum(result is not None for result in results) == 1
    events = store.snapshot(session)['events']
    if 'revision' in results:
        assert store.source_head_fenced(session, 'head')
        assert not any(event['kind'] == 'publication_started' for event in events)
        with pytest.raises(ValueError, match='fenc'):
            store.begin_remote_effect(session, 'publication_started', {'head': 'head'})
    else:
        assert not store.source_head_fenced(session, 'head')
        assert sum(event['kind'] == 'publication_started' for event in events) == 1
        assert not any(row['id'] == 'revision' for row in store.snapshot(session)['decisions'])


@pytest.mark.parametrize('peer_revises', [False, True])
def test_completed_but_unadvanced_source_job_cannot_start_correction_and_replay_is_idempotent(tmp_path, peer_revises):
    from concurrent.futures import ThreadPoolExecutor

    from mentat.autonomy_state import Store

    path = tmp_path / 'drain/state.db'
    store = Store(path)
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'head', {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'logical-final-item', 'head', 'base',
                               'plan', 'check', ['batch'], continuing=False)
    for index, role in enumerate(('code-reviewer', 'data-reviewer')):
        store.enqueue_job(session, 'ivl', role, f'source-{index}', f'input-{index}',
                          _source_job_payload('head', 'plan', 'check'))
    first = store.claim_job(session, 'worker-1', now=2)
    second = store.claim_job(session, 'worker-2', now=2)
    assert first and second
    revise = _review_result()
    peer = _review_result() if peer_revises else _review_result(decision='accept', corrections=[])
    feedback = []
    for index, (job, result) in enumerate(zip((first, second), (revise, peer), strict=True)):
        decision_id = f'decision-{index}'
        outcome, decision = _source_outcome_and_decision(
            job['id'], 'head', 'check', f'reviewer-{index}', decision_id, result)
        store.finish_job(job['id'], job['lease_token'], outcome, now=3)
        store.record_source_decision(session, 'cycle', decision)
        feedback.append({'job_id': job['id'], 'decision_id': decision_id, 'result': result})
    store.finish_transition(first['id'])
    correction = {'kind': 'generate_spec', 'target_head': 'head', 'accepted_spec_base': 'head',
                  'source_cycle_id': 'cycle', 'source_cycle_round': 1, 'feedback': feedback}

    def finalize():
        return store.finalize_source_correction(session, 'cycle', 'head', feedback,
                                                'correction', 'input', correction,
                                                actionable=True, max_attempts=3)

    assert finalize() is None
    assert not any(job['payload'].get('kind') == 'generate_spec' for job in store.snapshot(session)['jobs'])
    assert store.source_head_fenced(session, 'head')
    store.finish_transition(second['id'])
    forged = deepcopy(feedback)
    forged[0]['result']['summary'] = 'invented feedback'
    with pytest.raises(ValueError):
        store.finalize_source_correction(session, 'cycle', 'head', forged,
                                         'correction', 'input', correction,
                                         actionable=True, max_attempts=3)
    assert store.source_cycles(session)[0]['failed_passes'] == 0
    assert not any(job['payload'].get('kind') == 'generate_spec' for job in store.snapshot(session)['jobs'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        job_ids = [future.result() for future in (pool.submit(finalize), pool.submit(finalize))]
    assert job_ids[0] and job_ids[0] == job_ids[1]
    snapshot = store.snapshot(session)
    corrections = [job for job in snapshot['jobs'] if job['payload'].get('kind') == 'generate_spec']
    assert len(corrections) == 1 and corrections[0]['id'] == job_ids[0]
    assert corrections[0]['payload']['feedback'] == feedback
    assert sum(event['kind'] == 'source_correction_checkpoint' for event in snapshot['events']) == 1
    assert store.source_cycles(session)[0]['failed_passes'] == 1
    reopened = Store(path)
    assert reopened.source_head_fenced(session, 'head')
    assert reopened.source_cycles(session)[0]['failed_passes'] == 1
    assert reopened.finalize_source_correction(session, 'cycle', 'head', feedback,
                                               'correction', 'input', correction,
                                               actionable=True, max_attempts=3) == job_ids[0]


def test_source_failure_budget_persists_across_heads_and_resume(tmp_path):
    from mentat.autonomy_state import Store

    path = tmp_path / 'cycles/state.db'
    store = Store(path)
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'h0', {}, 1, 1000)
    for index in range(3):
        head = f'h{index}'
        store.register_source_pass(session, 'cycle', 'logical-final-item', head, 'base',
                                   f'plan-{index}', f'check-{index}', ['batch'], continuing=index > 0)
        for actor, role in enumerate(('code-reviewer', 'data-reviewer')):
            store.enqueue_job(session, 'ivl', role, f'source-{index}-{actor}', f'input-{index}-{actor}',
                              _source_job_payload(head, f'plan-{index}', f'check-{index}'))
        jobs = [store.claim_job(session, f'worker-{actor}', now=10 + index * 10) for actor in range(2)]
        assert all(jobs)
        feedback = []
        for actor, job in enumerate(jobs):
            result = _review_result() if actor == 0 else _review_result(decision='accept', corrections=[])
            reviewer = f'reviewer-{index}-{actor}'
            decision_id = f'decision-{index}-{actor}'
            outcome, decision = _source_outcome_and_decision(
                job['id'], head, f'check-{index}', reviewer, decision_id, result)
            store.finish_job(job['id'], job['lease_token'], outcome, now=11 + index * 10)
            store.record_source_decision(session, 'cycle', decision)
            store.finish_transition(job['id'])
            feedback.append({'job_id': job['id'], 'decision_id': decision_id, 'result': result})
        correction = {'kind': 'generate_spec', 'target_head': head, 'accepted_spec_base': head,
                      'source_cycle_id': 'cycle', 'source_cycle_round': index + 1, 'feedback': feedback}
        correction_id = store.finalize_source_correction(
            session, 'cycle', head, feedback, f'correction-{index}', f'digest-{index}', correction,
            actionable=True, max_attempts=3)
        assert store.source_cycles(session)[0]['failed_passes'] == index + 1
        assert store.source_head_fenced(session, head)
        if index < 2:
            assert correction_id
            job = store.claim_job(session, 'corrector', now=12 + index * 10)
            assert job and job['id'] == correction_id
            store.finish_job(job['id'], job['lease_token'], {'status': 'succeeded'}, now=13 + index * 10)
            store.finish_transition(job['id'])
            if index == 0:
                same_head = store.register_source_pass(
                    session, 'cycle', 'logical-final-item', head, 'base',
                    'plan-0', 'check-0', ['batch'], continuing=True)
                assert same_head['failed_passes'] == 1
                store.session_status(session, 'paused')
                store.resume_session(session, now=15, deadline=7215)
                store = Store(path)
                assert store.source_cycles(session)[0]['failed_passes'] == 1
        else:
            assert correction_id is None
    assert all(store.source_head_fenced(session, f'h{index}') for index in range(3))
    with pytest.raises(ValueError):
        store.register_source_pass(session, 'cycle', 'logical-final-item', 'h3', 'base',
                                   'plan-3', 'check-3', ['batch'], continuing=True)
    store.register_source_pass(session, 'other-cycle', 'other-item', 'other-head', 'base',
                               'other-plan', 'other-check', ['other-batch'], continuing=False)
    assert next(row for row in store.source_cycles(session) if row['id'] == 'other-cycle')['failed_passes'] == 0


def test_expired_source_lease_cannot_create_decision_or_fence(tmp_path):
    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'expired/state.db')
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'head', {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'logical-final-item', 'head', 'base',
                               'plan', 'check', ['batch'], continuing=False)
    payload = _source_job_payload('head', 'plan', 'check')
    job_id = store.enqueue_job(session, 'ivl', 'code-reviewer', 'source', 'input', payload)
    store.enqueue_job(session, 'ivl', 'data-reviewer', 'source-peer', 'peer-input', payload)
    job = store.claim_job(session, 'worker', now=2, lease_seconds=1)
    assert job and job['id'] == job_id
    with pytest.raises(ValueError, match='lease'):
        store.finish_job(job_id, job['lease_token'], {
            'status': 'succeeded', 'author_id': 'reviewer', 'result': {'decision': 'revise'}}, now=4)
    with pytest.raises(ValueError):
        store.record_source_decision(session, 'cycle', {
            'id': 'forged', 'job_id': job_id, 'head': 'head',
            'reviewer_id': 'reviewer', 'decision': 'revise'})
    assert not store.source_head_fenced(session, 'head')
    assert not store.snapshot(session)['decisions']


def test_partial_source_schedule_cannot_dispatch_and_resume_enqueues_missing_pair_once(tmp_path):
    from mentat.autonomy_state import Store

    path = tmp_path / 'partial/state.db'
    store = Store(path)
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'head', {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'logical-final-item', 'head', 'base',
                               'plan', 'check', ['batch-a', 'batch-b'], continuing=False)

    def enqueue(target, batch, role):
        return target.enqueue_job(session, 'ivl', role, f'{batch}-{role}', f'input-{batch}-{role}', {
            'kind': 'review_final_source', 'target_head': 'head', 'source_cycle_id': 'cycle',
            'source_batch': {'id': batch, 'plan_id': 'plan'}, 'check_key': 'check'})

    original = enqueue(store, 'batch-a', 'code-reviewer')
    enqueue(store, 'batch-a', 'data-reviewer')
    enqueue(store, 'batch-b', 'code-reviewer')
    assert store.claim_job(session, 'worker', now=2) is None
    reopened = Store(path)
    assert enqueue(reopened, 'batch-a', 'code-reviewer') == original
    assert reopened.claim_job(session, 'worker', now=3) is None
    enqueue(reopened, 'batch-b', 'data-reviewer')
    job = reopened.claim_job(session, 'worker', now=4)
    assert job and job['id'] == original
    assert len(reopened.source_pass_jobs(session, 'cycle', 'head')) == 4


def test_blocked_source_cycle_records_an_explicit_gap_without_other_findings(tmp_path):
    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'blocked/state.db')
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'head', {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'logical-final-item', 'head', 'base',
                               'plan', 'check', ['batch'], continuing=False)
    assert not store.snapshot(session)['findings']
    store.block_source_cycle(session, 'cycle', 'head', 'source reviewer deferred')
    snapshot = store.snapshot(session)
    assert store.source_head_fenced(session, 'head')
    assert any(row.get('status') == 'inconclusive' and 'deferred' in row.get('summary', '')
               for row in snapshot['findings'])
    assert not snapshot['claims']


def test_deferred_source_outcome_is_preserved_without_applying_changes(tmp_path):
    repo = repo_fixture.__wrapped__(tmp_path)
    _valid_publication_spec(repo)
    fas_path = repo / '.mentat/fas.json'
    fas = json.loads(fas_path.read_text())
    requirement = fas['requirements'][0]['id']
    for row in fas['surfaces']:
        if row['path'] == 'src.py':
            row.update(status='mapped', requirement_ids=[requirement])
    fas_path.write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline mapping')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', base)
    spec = repo / 'openspec/specs/app/spec.md'
    spec.write_text(spec.read_text() + '\nSource requires review.\n')
    git(repo, 'commit', '-qam', 'candidate CAS')
    proposed = [{'path': 'untrusted.py', 'operation': 'write', 'content': 'untrusted = True\n'}]
    defects = [{'id': 'defect', 'requirement_id': requirement, 'evidence_id': '',
                'summary': 'Potential application issue needs investigation.'}]

    class DeferredSourceAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            kind = job['payload']['kind']
            if kind == 'generate_spec':
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'No specification changes', 'findings': [],
                    'verification_requests': []}}
            if kind == 'review_final_source':
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'defer', 'summary': json.dumps({
                        'schema_version': 2, 'findings': 'Potential application issue.',
                        'unresolved_dependencies': ['Independent investigation required.'],
                        'spec_corrections': []}), 'findings': [], 'requirements': [], 'claims': [],
                    'verification_requests': [], 'defects': defects, 'changes': proposed}}
            return super().run(repo, job, output, timeout_seconds=timeout_seconds)

    snapshot, _, github = run(repo, tmp_path, agent=DeferredSourceAgent())
    assert snapshot['session']['status'] == 'blocked'
    assert not github.published and not github.merged
    assert not any(job['payload']['kind'] == 'review_final' for job in snapshot['jobs'])
    assert not any(job['payload']['kind'] == 'generate_spec' and job['payload'].get('source_cycle_round', 0) > 0
                   for job in snapshot['jobs'])
    outcomes = [job['outcome']['result'] for job in snapshot['jobs']
                if job['payload']['kind'] == 'review_final_source' and job.get('outcome')
                and job['outcome'].get('status') == 'succeeded']
    assert outcomes
    assert all(result['changes'] == proposed and result['defects'] == defects for result in outcomes)
    assert not (repo / 'untrusted.py').exists()
    assert all(not (Path(job['outcome']['worktree']) / 'untrusted.py').exists()
               for job in snapshot['jobs'] if job['payload']['kind'] == 'review_final_source'
               and job.get('outcome', {}).get('status') == 'succeeded')


def test_source_drain_checkpoints_git_runtime_failure_without_scheduling(tmp_path, monkeypatch):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'git-failure/state.db')
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'head', {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'logical-final-item', 'head', 'base',
                               'plan', 'check', ['batch'], continuing=False)
    for index, role in enumerate(('code-reviewer', 'data-reviewer')):
        store.enqueue_job(session, 'ivl', role, f'source-{index}', f'input-{index}',
                          _source_job_payload('head', 'plan', 'check'))
    jobs = [store.claim_job(session, f'worker-{index}', now=2) for index in range(2)]
    assert all(jobs)
    for index, job in enumerate(jobs):
        outcome, decision = _source_outcome_and_decision(
            job['id'], 'head', 'check', f'reviewer-{index}', f'decision-{index}',
            _review_result(decision='accept', corrections=[]))
        decision.update(review_kind='review_final_source', author_id='author', status='passed')
        store.finish_job(job['id'], job['lease_token'], outcome, now=3)
        store.record_source_decision(session, 'cycle', decision)
        store.finish_transition(job['id'])
    coordinator = Coordinator(tmp_path / 'repo', store, Agent(), None, None)
    coordinator.session_id = session
    def fail_git(*args, **kwargs):
        raise RuntimeError('Git unavailable')
    monkeypatch.setattr(coordinator, '_validate_source_input', fail_git)
    coordinator._drain_source_cycles()
    snapshot = store.snapshot(session)
    assert store.source_head_fenced(session, 'head')
    assert any(row.get('status') == 'inconclusive' and 'Git unavailable' in row.get('summary', '')
               for row in snapshot['findings'])
    assert not any(row['payload'].get('kind') in {'generate_spec', 'review_final'} for row in snapshot['jobs'])


def test_failed_aggregate_schedule_blocks_resolved_source_pass_once(tmp_path, monkeypatch):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'aggregate-failure/state.db')
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'head', {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'logical-final-item', 'head', 'base',
                               'plan', 'check', ['batch'], continuing=False)
    payload = _source_job_payload('head', 'plan', 'check')
    payload['proposal'].update(author_id='author', prior_authors=[])
    for index, role in enumerate(('code-reviewer', 'data-reviewer')):
        store.enqueue_job(session, 'ivl', role, f'source-{index}', f'input-{index}', payload)
    jobs = [store.claim_job(session, f'worker-{index}', now=2) for index in range(2)]
    assert all(jobs)
    for index, job in enumerate(jobs):
        outcome, decision = _source_outcome_and_decision(
            job['id'], 'head', 'check', f'reviewer-{index}', f'decision-{index}',
            _review_result(decision='accept', corrections=[]))
        decision.update(review_kind='review_final_source', author_id='author', status='passed')
        store.finish_job(job['id'], job['lease_token'], outcome, now=3)
        store.record_source_decision(session, 'cycle', decision)
        store.finish_transition(job['id'])
    store.put_record(session, 'evidence', {
        'id': 'evidence', 'head': 'head', 'base': 'base', 'check_key': 'check', 'status': 'passed'})
    coordinator = Coordinator(tmp_path / 'repo', store, Agent(), None, None)
    coordinator.session_id = session
    monkeypatch.setattr(coordinator, '_validate_source_input', lambda *args: None)
    monkeypatch.setattr(coordinator, '_final_source_coverage', lambda *args: {'batches': []})
    attempts = []
    def fail_enqueue(*args, **kwargs):
        attempts.append('attempt')
        raise RuntimeError('aggregate enqueue failed')
    monkeypatch.setattr(coordinator, '_enqueue_reviewers', fail_enqueue)
    coordinator._drain_source_cycles()
    assert store.source_head_fenced(session, 'head')
    assert store.source_cycles(session)[0]['status'] == 'blocked'
    assert any('aggregate enqueue failed' in row.get('summary', '')
               for row in store.snapshot(session)['findings'])
    coordinator._drain_source_cycles()
    assert attempts == ['attempt']
    assert not any(row['payload'].get('kind') == 'review_final' for row in store.snapshot(session)['jobs'])


def test_aggregate_revision_continues_existing_source_cycle_budget(tmp_path):
    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'aggregate-cycle/state.db')
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'h0', {}, 1, 1000)

    def source_pair(head, index, revise):
        plan, check = f'plan-{index}', f'check-{index}'
        store.register_source_pass(session, 'cycle', 'logical-final-item', head, 'base',
                                   plan, check, ['batch'], continuing=index > 0)
        for actor, role in enumerate(('code-reviewer', 'data-reviewer')):
            store.enqueue_job(session, 'ivl', role, f'source-{index}-{actor}', f'input-{index}-{actor}',
                              _source_job_payload(head, plan, check))
        jobs = [store.claim_job(session, f'worker-{actor}', now=10 + index * 10) for actor in range(2)]
        assert all(jobs)
        feedback = []
        for actor, job in enumerate(jobs):
            result = _review_result() if revise and actor == 0 else _review_result(decision='accept', corrections=[])
            decision_id = f'decision-{index}-{actor}'
            outcome, decision = _source_outcome_and_decision(
                job['id'], head, check, f'reviewer-{index}-{actor}', decision_id, result)
            store.finish_job(job['id'], job['lease_token'], outcome, now=11 + index * 10)
            store.record_source_decision(session, 'cycle', decision)
            store.finish_transition(job['id'])
            feedback.append({'job_id': job['id'], 'decision_id': decision_id, 'result': result})
        return feedback

    feedback = source_pair('h0', 0, True)
    correction = {'kind': 'generate_spec', 'target_head': 'h0', 'accepted_spec_base': 'h0',
                  'source_cycle_id': 'cycle', 'source_cycle_round': 1, 'feedback': feedback}
    correction_id = store.finalize_source_correction(
        session, 'cycle', 'h0', feedback, 'correction-0', 'digest-0', correction,
        actionable=True, max_attempts=3)
    assert correction_id
    job = store.claim_job(session, 'corrector', now=12)
    assert job and job['id'] == correction_id
    store.finish_job(job['id'], job['lease_token'], {'status': 'succeeded'}, now=13)
    store.finish_transition(job['id'])
    source_pair('h1', 1, False)
    store.resolve_source_pass(session, 'cycle', 'h1')
    for index, role in enumerate(('code-reviewer', 'data-reviewer')):
        store.enqueue_job(session, 'ivl', role, f'aggregate-{index}', f'aggregate-input-{index}', {
            'kind': 'review_final', 'target_head': 'h1', 'source_cycle_id': 'cycle'})
    store.mark_source_aggregate_scheduled(session, 'cycle', 'h1')
    continued = store.register_source_pass(session, 'cycle', 'logical-final-item', 'h2', 'base',
                                           'plan-2', 'check-2', ['batch'], continuing=True)
    assert continued['failed_passes'] == 1 and continued['active_head'] == 'h2'
    replayed = store.register_source_pass(session, 'cycle', 'logical-final-item', 'h2', 'base',
                                         'plan-2', 'check-2', ['batch'], continuing=True)
    assert replayed['failed_passes'] == 1
    assert store.source_head_fenced(session, 'h0')


def test_source_cycle_rejects_sibling_git_head_without_resetting_budget(tmp_path, monkeypatch):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    repo = repo_fixture.__wrapped__(tmp_path)
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('first branch\n')
    git(repo, 'commit', '-qam', 'first branch')
    first = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', '--detach', base)
    (repo / 'src.py').write_text('sibling branch\n')
    git(repo, 'commit', '-qam', 'sibling branch')
    sibling = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'sibling-state/state.db')
    session = store.new_session(str(repo), base, first, {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'logical-final-item', first, base,
                               'plan', 'check', ['batch'], continuing=False)
    coordinator = Coordinator(repo, store, Agent(), None, None)
    coordinator.session_id = session
    # Isolate candidate ancestry; the real Git commits above are divergent.
    monkeypatch.setattr(coordinator, '_final_source_plan', lambda *_: {'id': 'next-plan'})
    monkeypatch.setattr(coordinator, '_final_source_batches', lambda *_: [{'id': 'next-batch'}])
    job = {'loop': 'ivl', 'item_id': 'logical-final-item', 'payload': {'source_cycle_id': 'cycle'}}
    with pytest.raises(ValueError, match='descendant|ancestor'):
        coordinator._start_final_sources(job, {'head': sibling, 'parent': base}, {'check_key': 'check'})
    cycle = store.source_cycles(session)[0]
    assert cycle['active_head'] == first and cycle['failed_passes'] == 0
    assert not store.snapshot(session)['jobs']


def test_missing_source_cycle_lineage_rejects_descendant_but_not_unrelated_item(tmp_path, monkeypatch):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    repo = repo_fixture.__wrapped__(tmp_path)
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('reviewed source\n')
    git(repo, 'commit', '-qam', 'reviewed source')
    reviewed = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('corrected source\n')
    git(repo, 'commit', '-qam', 'corrected source')
    descendant = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'missing-lineage/state.db')
    session = store.new_session(str(repo), base, reviewed, {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'original-item', reviewed, base,
                               'plan', 'check', ['batch'], continuing=False)
    coordinator = Coordinator(repo, store, Agent(), None, None)
    coordinator.session_id = session
    monkeypatch.setattr(coordinator, '_final_source_plan', lambda *_: {'id': 'next-plan'})
    monkeypatch.setattr(coordinator, '_final_source_batches', lambda *_: [
        {'id': 'next-batch', 'obligations': []}])
    scheduled = []
    monkeypatch.setattr(coordinator, '_enqueue_reviewers', lambda *args: scheduled.append(args))
    proposal = {'head': descendant, 'parent': base}
    evidence = {'check_key': 'next-check'}
    job = {'loop': 'ivl', 'item_id': 'original-item', 'payload': {}}
    with pytest.raises(ValueError, match='missing correction lineage'):
        coordinator._start_final_sources(job, proposal, evidence)
    assert len(store.source_cycles(session)) == 1
    assert store.source_cycles(session)[0]['active_head'] == reviewed
    assert not scheduled

    coordinator._start_final_sources({**job, 'item_id': 'unrelated-item'}, proposal, evidence)
    cycles = store.source_cycles(session)
    assert {cycle['root_item'] for cycle in cycles} == {'original-item', 'unrelated-item'}
    assert next(cycle for cycle in cycles if cycle['root_item'] == 'unrelated-item')['failed_passes'] == 0
    assert len(scheduled) == 1
