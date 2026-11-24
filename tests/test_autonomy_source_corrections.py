"""Source review revisions must route to current-head specification correction."""

import json
import threading
from copy import deepcopy
from pathlib import Path

import pytest
from test_autonomy import Agent, _valid_publication_spec, git, run
from test_autonomy import repo as repo
from test_autonomy import repo as repo_fixture
from test_autonomy_spec_surface import seeded as seeded


@pytest.mark.parametrize('always_malformed', [False, True])
def test_malformed_source_review_retries_before_recording_success(seeded, tmp_path, monkeypatch, always_malformed):
    from test_autonomy import GitHub, Worker

    from mentat import autonomy_runtime
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    repo, store, _, _, head = seeded
    local = threading.local()
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
        calls.append((job['id'], job['role'], job['attempt']))
        detail = {'schema_version': 2, 'findings': 'The source matches its observation.',
                  'unresolved_dependencies': [], 'spec_corrections': []}
        if job['role'] == 'code-reviewer' and (always_malformed or job['attempt'] == 1):
            detail.update(validation='checked', scope='src.py')
        answer = {'schema_version': 1, 'decision': 'accept', 'summary': json.dumps(detail),
                  'requirements': [], 'claims': [], 'findings': [], 'verification_requests': [],
                  'defects': [], 'changes': []}
        Path(argv[argv.index('--output-last-message') + 1]).write_text(json.dumps(answer))
        return json.dumps({'type': 'thread.started', 'thread_id': f"{job['id']}-{job['attempt']}"}) + '\n'

    monkeypatch.setattr(autonomy_runtime, '_run', fake_run)
    monkeypatch.setattr(autonomy_runtime.CodexAgent, '_execute', fake_execute)

    class ReviewAgent(Agent):
        def run(self, worktree, job, output, *, timeout_seconds):
            kind = job['payload']['kind']
            if kind == 'review_final_source':
                local.job = job
                return autonomy_runtime.CodexAgent().run(worktree, job, output, timeout_seconds=timeout_seconds)
            if kind in {'generate_spec', 'generate_verification'}:
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'No changes', 'findings': [],
                    'verification_requests': []}}
            return super().run(worktree, job, output, timeout_seconds=timeout_seconds)

    github = GitHub()
    snapshot = Coordinator(repo, store, ReviewAgent(), Worker(), github).run(spec_surface='src.py')
    code = [row for row in calls if row[1] == 'code-reviewer']
    sibling = [row for row in calls if row[1] == 'data-reviewer']
    assert len({row[0] for row in code}) == len({row[0] for row in sibling}) == 1
    assert code[0][0] != sibling[0][0] and [row[2] for row in sibling] == [1]
    expected_attempts = [1, 2, 3] if always_malformed else [1, 2]
    assert [row[2] for row in code] == expected_attempts
    job_id = code[0][0]
    finished = [event['payload'] for event in snapshot['events']
                if event['kind'] == 'job_finished' and event['payload']['job_id'] == job_id]
    assert [event['attempt'] for event in finished] == expected_attempts
    assert all(event['status'] == 'queued' and event['outcome']['status'] == 'failed'
               for event in finished[:-1])
    for attempt in expected_attempts:
        folder = store.path.parent / 'agents' / job_id / str(attempt)
        assert list(folder.glob('manifest-*.json')) and list(folder.glob('prompt-*.txt'))
        result = json.loads((folder / 'codex-result.json').read_text())
        detail = json.loads(result['summary'])
        assert ('validation' in detail) == (always_malformed or attempt == 1)
    decisions = [row for row in snapshot['decisions'] if row.get('job_id') == job_id]
    if always_malformed:
        assert not decisions and not github.published and not github.merged
        assert snapshot['session']['status'] == 'blocked'
    else:
        assert finished[-1]['status'] == 'succeeded'
        assert len(decisions) == 1 and decisions[0]['decision'] == 'accept'
        assert github.published == [head] and len(github.merged) == 1
        pair = [row for row in snapshot['decisions'] if row.get('review_kind') == 'review_final_source']
        assert len(pair) == 2 and len({row['reviewer_id'] for row in pair}) == 2
    assert Store(store.path).snapshot(snapshot['session']['id'])['events'] == snapshot['events']


@pytest.mark.parametrize('peer_defers', [False, True])
@pytest.mark.parametrize('review_order', ['first', 'last'])
@pytest.mark.parametrize('correction_defers', [False, True])
@pytest.mark.parametrize(('scl_revise', 'scl_peer'), [
    (False, 'accept'), (True, 'accept'), (True, 'revise'), (True, 'defer'), (True, 'failed'),
])
def test_source_revision_routes_to_current_head_specification_correction(
    tmp_path, monkeypatch, peer_defers, review_order, correction_defers, scl_revise, scl_peer
):
    from mentat import autonomy_runtime

    repo = repo_fixture.__wrapped__(tmp_path)
    if correction_defers:
        for index in range(14):
            (repo / f'large-{index}.py').write_text('# ' + 'x' * 65000 + '\n')
    _valid_publication_spec(repo)
    fas_path = repo / '.mentat/fas.json'
    fas = json.loads(fas_path.read_text())
    requirement = fas['requirements'][0]['id']
    if correction_defers:
        fas['surfaces'].extend({'path': f'large-{index}.py', 'status': 'gap', 'requirement_ids': []}
                               for index in range(14))
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
    spec_code_returned = threading.Event()
    state = {'source_revised': False, 'corrected': False, 'drain': None, 'code_job': None,
             'correction_deferred': False, 'source_head': None, 'stale_descendant_seen': False,
             'scl_revised': False, 'spec_code_job': None, 'scl_first_head': None,
             'first_scl_terminal': None, 'reviewed_surface': None}
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
        if payload['kind'] == 'review_spec':
            first_slot = job['item_id'].endswith('.1')
            proposal_head = payload['proposal']['head']
            with lock:
                if first_slot and state['scl_first_head'] is None:
                    state['scl_first_head'] = proposal_head
                first_cohort = state['scl_first_head'] in {None, proposal_head}
                if first_slot and first_cohort:
                    state['spec_code_job'] = job['id']
                    spec_code_returned.set()
            if not first_slot and first_cohort:
                assert spec_code_returned.wait(timeout=15)
                import time

                from mentat.autonomy_state import Store

                review_store = Store(tmp_path / 'state/state.db')
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    review_jobs = review_store.snapshot()['jobs']
                    first = next((row for row in review_jobs if row['id'] == state['spec_code_job']), None)
                    if first and (first['status'] == 'failed' or
                                  (first['status'] == 'succeeded' and first.get('outcome'))):
                        state['first_scl_terminal'] = deepcopy(first)
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError('first SCL reviewer did not persist its terminal outcome')
            with lock:
                initial = state['scl_first_head'] == proposal_head
                selected = first_slot == (review_order == 'first')
                revise_scl = initial and (selected or scl_peer == 'revise')
                if revise_scl and selected:
                    state['scl_revised'] = True
                scl_decision = ('revise' if revise_scl else
                                'defer' if initial and not selected and scl_peer == 'defer' else 'accept')
            if initial and not selected and scl_peer == 'failed':
                raise RuntimeError('SCL peer unavailable')
            review_finding = 'Specify integer return.'
            if revise_scl and correction_defers:
                accepted = payload['proposal']['accepted_spec_base']
                before = json.loads(git(repo, 'show', f'{accepted}:.mentat/fas.json'))
                after = json.loads(git(repo, 'show', f'{proposal_head}:.mentat/fas.json'))
                old = {row['path']: row for row in before['surfaces']}
                changed = [row['path'] for row in after['surfaces'] if old.get(row['path']) != row]
                assert len(changed) == 1
                review_finding += f' Clarify the admitted observation for {changed[0]}.'
                with lock:
                    state['reviewed_surface'] = changed[0]
            answer = {'schema_version': 1, 'decision': scl_decision,
                      'summary': 'Clarify the current source observation.',
                      'requirements': [], 'claims': [], 'verification_requests': [], 'defects': [],
                      'changes': [], 'findings': [{'id': 'scl-detail', 'summary': review_finding,
                                                    'requirement_ids': [requirement], 'severity': 'medium'}]
                      if revise_scl else []}
            Path(argv[argv.index('--output-last-message') + 1]).write_text(json.dumps(answer))
            return json.dumps({'type': 'thread.started', 'thread_id': job['id']}) + '\n'
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
                with lock:
                    if state['source_head'] is None:
                        state['source_head'] = git(repo, 'rev-parse', 'HEAD')
                local.job = job
                return autonomy_runtime.CodexAgent().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'review_spec' and scl_revise and revised:
                local.job = job
                return autonomy_runtime.CodexAgent().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'generate_spec':
                if revised:
                    if correction_defers and not state['correction_deferred']:
                        state['correction_deferred'] = True
                        return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                            'decision': 'defer', 'summary': 'Continue with remaining context',
                            'findings': [], 'verification_requests': []}}
                    from mentat.autonomy_state import Store

                    store = Store(tmp_path / 'state/state.db')
                    live_snapshot = store.snapshot()
                    for prior in live_snapshot['jobs']:
                        if (prior['id'] != job['id'] and prior['payload'].get('kind') == 'generate_spec'
                                and prior['payload'].get('source_cycle_id') == payload.get('source_cycle_id')
                                and prior['payload'].get('target_head') != state['source_head']):
                            assert store.source_job_fenced(live_snapshot['session']['id'], prior)
                            state['stale_descendant_seen'] = True
                    old_jobs = [row for row in live_snapshot['jobs']
                                if row['payload'].get('kind') == 'review_final_source'
                                and row['payload'].get('target_head') == payload['target_head']]
                    with lock:
                        if state['drain'] is None:
                            state['drain'] = [(row['id'], row['status'], store.transition_status(row['id']))
                                              for row in old_jobs]
                    path = repo / '.mentat/fas.json'
                    current = json.loads(path.read_text())
                    context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
                    included = set(context['files'])
                    source_row = next(row for row in current['surfaces'] if row['path'] == 'src.py')
                    envelope = context.get('correction_scope')
                    allowed_surfaces = set(envelope['records']['surfaces']) if envelope else None
                    if envelope and 'src.py' not in allowed_surfaces:
                        candidates = [row for row in current['surfaces']
                                      if row['path'] in allowed_surfaces and row['path'] in included]
                        if not candidates:
                            return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                                'decision': 'defer', 'summary': 'Reviewed source not included',
                                'findings': [], 'verification_requests': []}}
                        assert len(candidates) == 1
                        if correction_defers:
                            assert candidates[0]['path'] == state['reviewed_surface']
                        candidates[0]['note'] = 'Clarified the admitted source observation.'
                    elif (scl_revise and state['scl_revised'] and 'src.py' in included
                            and source_row.get('note') != 'The selected function returns the integer 1.'):
                        source_row['note'] = 'The selected function returns the integer 1.'
                        with lock:
                            state['corrected'] = True
                    elif 'src.py' in included and not state['corrected']:
                        next(row for row in current['surfaces'] if row['path'] == 'src.py')['note'] = (
                            'The selected function returns 1.')
                        with lock:
                            state['corrected'] = True
                    else:
                        selected = next(row for row in current['surfaces']
                                        if row['path'].startswith('large-') and row['path'] in included
                                        and not row.get('note'))
                        selected['note'] = 'Current source was included in this batch.'
                    path.write_text(json.dumps(current))
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
    if scl_revise:
        first_reviews = [job for job in snapshot['jobs']
                         if job['payload'].get('kind') == 'review_spec'
                         and job['payload'].get('source_cycle_round')
                         and job['payload'].get('proposal', {}).get('round', 0) == 0
                         and job['payload'].get('proposal', {}).get('head') == state['scl_first_head']]
        assert len(first_reviews) == 2
        first_terminal = state['first_scl_terminal']
        assert first_terminal is not None and first_terminal['item_id'].endswith('.1')
        if scl_peer == 'failed' and review_order == 'last':
            assert first_terminal['attempt'] == 3
            assert first_terminal['status'] == 'failed' or first_terminal['outcome'].get('exhausted') is True
        assert len({review['payload']['review_cohort'] for review in first_reviews}) == 1
        expected_slot = '.1' if review_order == 'first' else '.2'
        for review in first_reviews:
            expected = 'revise' if review['item_id'].endswith(expected_slot) else scl_peer
            if expected == 'failed':
                assert review['attempt'] == 3
                assert review['status'] == 'failed' or review['outcome'].get('exhausted') is True
                assert not any(row.get('job_id') == review['id'] for row in snapshot['decisions'])
            else:
                assert review['status'] == 'succeeded'
                assert review['outcome']['result']['decision'] == expected
                assert any(row.get('job_id') == review['id'] and row.get('decision') == expected
                           for row in snapshot['decisions'])
    if scl_revise and scl_peer in {'defer', 'failed'}:
        if scl_peer == 'failed':
            from mentat.autonomy_state import Store

            cycles = Store(tmp_path / 'state/state.db').source_cycles(snapshot['session']['id'])
            assert cycles[0]['status'] == 'blocked'
            assert any('SCL' in row.get('summary', '') for row in snapshot['findings'])
        assert not github.published and not github.merged
        assert not any(job['payload'].get('kind') == 'generate_spec'
                       and job['payload'].get('source_cycle_round')
                       and job['payload'].get('round', 0) > 0 for job in snapshot['jobs'])
        assert snapshot['session']['status'] == 'blocked'
        return
    corrections = [(payload, head) for kind, payload, head in calls
                   if kind == 'generate_spec' and payload.get('source_cycle_round', 0) > 0]
    assert len(corrections) >= (2 if correction_defers else 1), snapshot
    if not correction_defers:
        assert len(corrections) == (2 if scl_revise else 1)
    payload, correction_head = corrections[0]
    if correction_defers:
        resumed = corrections[1][0]
        assert resumed['target_head'] == first_head
        assert resumed['targetfiles'] and resumed['feedback'] == payload['feedback']
        assert resumed['source_cycle_id'] == payload['source_cycle_id']
        assert resumed['source_cycle_round'] == payload['source_cycle_round']
        assert state['stale_descendant_seen'], 'an accepted partial descendant must hand off before completion'
    if scl_revise:
        retry, retry_head = next(item for item in corrections
                                 if item[0].get('scl_review_feedback')
                                 and item[0].get('round') == 1
                                 and item[0].get('target_head') == state['scl_first_head'])
        assert retry_head == retry['target_head'] != first_head
        assert retry['accepted_spec_base'] == first_head
        assert retry['review_base'] == payload['review_base']
        assert retry['feedback'] == payload['feedback']
        assert retry['source_cycle_id'] == payload['source_cycle_id']
        assert retry['source_cycle_round'] == payload['source_cycle_round']
        assert retry['round'] == payload.get('round', 0) + 1
        assert 'Specify integer return.' in json.dumps(retry['scl_review_feedback'])
        assert 'Clarify the current source observation.' in json.dumps(retry['scl_review_feedback'])
        review_decisions = [row for row in snapshot['decisions'] if row.get('review_kind') == 'review_spec'
                            and row.get('head') == retry_head]
        assert {row['decision'] for row in review_decisions} == {'revise', scl_peer}
        review_feedback = retry['scl_review_feedback']
        assert [row['job_id'] for row in review_feedback] == sorted(row['job_id'] for row in review_feedback)
        assert {row['decision_id'] for row in review_feedback} == {row['id'] for row in review_decisions}
        for entry in review_feedback:
            review_job = next(job for job in first_reviews if job['id'] == entry['job_id'])
            decision = next(row for row in review_decisions if row['id'] == entry['decision_id'])
            assert entry['result'] == review_job['outcome']['result']
            assert entry['reviewer_id'] == decision['reviewer_id']
            assert entry['review_cohort'] == decision['review_cohort']
        assert 'Specify integer return.' in retry['prompt']
        assert 'Narrow the note to the observed return value.' in retry['prompt']
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
        'The selected function returns the integer 1.' if scl_revise else 'The selected function returns 1.')


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


@pytest.mark.parametrize('remote_head', ['head', 'foreign-head'])
def test_source_revision_fences_remote_effect_atomically(tmp_path, remote_head):
    from concurrent.futures import ThreadPoolExecutor

    from mentat.autonomy_state import SourceBarrierHeld, Store

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
            store.begin_remote_effect(session, 'publication_started', {'head': remote_head})
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
        with pytest.raises(SourceBarrierHeld, match='holds remote effects'):
            store.begin_remote_effect(session, 'publication_started', {'head': remote_head})
        assert not any(event['kind'] == 'publication_started' for event in store.snapshot(session)['events'])
    else:
        assert not store.source_head_fenced(session, 'head')
        assert sum(event['kind'] == 'publication_started' for event in events) == 1
        assert not any(row['id'] == 'revision' for row in store.snapshot(session)['decisions'])


@pytest.mark.parametrize('remote_head', ['head', 'foreign-head'])
def test_active_source_correction_blocks_remote_effect_without_start_event(tmp_path, remote_head):
    from mentat.autonomy_state import SourceBarrierHeld, Store

    store = Store(tmp_path / 'barrier-wins/state.db')
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'head', {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'item', 'head', 'base', 'plan', 'check', ['batch'],
                               continuing=False)
    store.block_source_cycle(session, 'cycle', 'head', 'source correction unresolved')
    with pytest.raises(SourceBarrierHeld, match='holds remote effects'):
        store.begin_remote_effect(session, 'publication_started', {'head': remote_head})
    assert not any(event['kind'] == 'publication_started' for event in store.snapshot(session)['events'])


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
            payload = _source_job_payload(head, f'plan-{index}', f'check-{index}')
            payload['source_epoch'] = store.source_finalization_epoch(session, payload)
            store.enqueue_job(session, 'ivl', role, f'source-{index}-{actor}', f'input-{index}-{actor}',
                              payload)
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
    with pytest.raises(ValueError):
        store.register_source_pass(session, 'other-cycle', 'other-item', 'other-head', 'base',
                                   'other-plan', 'other-check', ['other-batch'], continuing=False)
    assert len(store.source_cycles(session)) == 1
    store.session_status(session, 'paused')
    store.resume_session(session, now=50, deadline=7250)
    reopened = Store(path)
    with pytest.raises(ValueError):
        reopened.begin_remote_effect(session, 'publication_started', {'head': 'other-head'})
    assert not any(event['kind'] == 'publication_started' for event in reopened.snapshot(session)['events'])


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
            payload = _source_job_payload(head, plan, check)
            payload['source_epoch'] = store.source_finalization_epoch(session, payload)
            store.enqueue_job(session, 'ivl', role, f'source-{index}-{actor}', f'input-{index}-{actor}',
                              payload)
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
        payload = {'kind': 'review_final', 'target_head': 'h1', 'source_cycle_id': 'cycle'}
        payload['source_epoch'] = store.source_finalization_epoch(session, payload)
        store.enqueue_job(session, 'ivl', role, f'aggregate-{index}', f'aggregate-input-{index}', payload)
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


def test_missing_source_cycle_lineage_rejects_descendant_but_allows_early_work(tmp_path, monkeypatch):
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

    early = store.enqueue_job(session, 'iil', 'debugger', 'unrelated-investigation', 'input',
                              {'kind': 'investigate_defect', 'target_head': descendant})
    assert store.claim_job(session, 'independent-worker', now=2)['id'] == early
    assert len(store.source_cycles(session)) == 1


@pytest.mark.parametrize('branch', ['descendant', 'sibling', 'changed-note'])
@pytest.mark.parametrize('operation', ['source', 'aggregate', 'publication', 'merge'])
def test_unresolved_correction_blocks_foreign_finalization_across_git_heads(tmp_path, monkeypatch, branch, operation):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    repo = repo_fixture.__wrapped__(tmp_path)
    fas = repo / '.mentat/fas.json'
    fas.write_text(json.dumps({'surfaces': [{'path': 'src.py', 'note': 'challenged claim'}]}))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'challenged correspondence')
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('def value(): return 2\n')
    git(repo, 'commit', '-qam', 'reviewed candidate')
    reviewed = git(repo, 'rev-parse', 'HEAD')
    if branch == 'sibling':
        git(repo, 'checkout', '-q', '--detach', base)
    content = json.loads(fas.read_text())
    if branch == 'changed-note':
        content['surfaces'][0]['note'] = 'different unsupported claim'
    else:
        content['other_metadata'] = 'unrelated edit'
    fas.write_text(json.dumps(content))
    git(repo, 'commit', '-qam', 'foreign work')
    foreign = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'barrier/state.db')
    session = store.new_session(str(repo), base, reviewed, {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'challenged-item', reviewed, base,
                               'plan', 'check', ['batch'], continuing=False)
    store.block_source_cycle(session, 'cycle', reviewed, 'source correction needs adjudication')
    coordinator = Coordinator(repo, store, Agent(), None, None)
    coordinator.session_id = session
    monkeypatch.setattr(coordinator, '_final_source_plan', lambda *_: {'id': 'foreign-plan'})
    monkeypatch.setattr(coordinator, '_final_source_batches', lambda *_: [
        {'id': 'foreign-batch', 'obligations': []}])
    monkeypatch.setattr(coordinator, '_enqueue_reviewers', lambda *_: None)
    with pytest.raises(ValueError):
        if operation == 'source':
            coordinator._start_final_sources(
                {'loop': 'ivl', 'item_id': 'foreign-item', 'payload': {}},
                {'head': foreign, 'parent': base}, {'check_key': 'foreign-check'})
        elif operation == 'aggregate':
            store.enqueue_job(session, 'ivl', 'code-reviewer', 'foreign-final', 'input',
                              {'kind': 'review_final', 'target_head': foreign})
        else:
            store.begin_remote_effect(session, f'{operation}_started', {'head': foreign})
    snapshot = store.snapshot(session)
    assert len(store.source_cycles(session)) == 1
    assert not any(event['kind'] in {'publication_started', 'merge_started'} for event in snapshot['events'])
    assert not any(job['payload'].get('kind') == 'review_final' for job in snapshot['jobs'])


@pytest.mark.parametrize('retry_kind', ['defer', 'capacity'])
def test_authorized_correction_hands_off_frontier(tmp_path, retry_kind):
    from mentat.autonomy import Coordinator, ReviewContextCapacity, default_config
    from mentat.autonomy_state import Store

    repo = repo_fixture.__wrapped__(tmp_path)
    head = git(repo, 'rev-parse', 'HEAD')
    (repo / 'src.py').write_text('def value(): return 3\n')
    git(repo, 'commit', '-qam', 'unreviewed partial correction')
    candidate = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'handoff/state.db')
    config = default_config(repo)
    session = store.new_session(str(repo), head, head, config, 1, 1000)
    store.register_source_pass(session, 'cycle', 'logical-final-item', head, head,
                               'plan', 'check', ['batch'], continuing=False)
    for index, role in enumerate(('code-reviewer', 'data-reviewer')):
        store.enqueue_job(session, 'ivl', role, f'source-{index}', f'input-{index}',
                          _source_job_payload(head, 'plan', 'check'))
    jobs = [store.claim_job(session, f'worker-{index}', now=2) for index in range(2)]
    feedback = []
    for index, source_job in enumerate(jobs):
        result = _review_result() if index == 0 else _review_result(decision='accept', corrections=[])
        decision_id = f'decision-{index}'
        outcome, decision = _source_outcome_and_decision(
            source_job['id'], head, 'check', f'reviewer-{index}', decision_id, result)
        store.finish_job(source_job['id'], source_job['lease_token'], outcome, now=3)
        store.record_source_decision(session, 'cycle', decision)
        store.finish_transition(source_job['id'])
        feedback.append({'job_id': source_job['id'], 'decision_id': decision_id, 'result': result})
    payload = {'kind': 'generate_spec', 'target_head': head, 'accepted_spec_base': head,
               'review_base': head, 'source_cycle_id': 'cycle', 'source_cycle_round': 1,
               'feedback': feedback, 'targetfiles': ['src.py', 'other.py'], 'batch_limit': 3}
    correction_id = store.finalize_source_correction(
        session, 'cycle', head, feedback, 'correction', 'input', payload,
        actionable=True, max_attempts=3)
    correction = store.claim_job(session, 'corrector', now=4)
    assert correction and correction['id'] == correction_id
    outcome = {'status': 'succeeded', 'head': head, 'context_pending': ['src.py'],
               'context_included': ['other.py'], 'context_unavailable': [],
               'result': {'decision': 'defer', 'summary': 'Continue with smaller frontier', 'findings': []}}
    if retry_kind == 'capacity':
        outcome['head'] = candidate
        outcome['result']['decision'] = 'accept'
    store.finish_job(correction_id, correction['lease_token'], outcome, now=5)
    coordinator = Coordinator(repo, store, Agent(), None, None)
    coordinator.session_id = session
    coordinator.config = config
    proposal = {'head': candidate, 'parent': head, 'accepted_spec_base': head, 'round': 0,
                'root_item': 'logical-final-item', 'author_id': 'corrector'}
    def advance():
        if retry_kind == 'defer':
            coordinator._advance(correction, outcome)
        else:
            coordinator._handle_spec_capacity(correction, proposal, ReviewContextCapacity(
                'review does not fit', {'src.py'}))
    advance()
    successors = [job for job in store.snapshot(session)['jobs']
                  if job['payload'].get('kind') == 'generate_spec' and job['id'] != correction_id]
    assert len(successors) == 1
    successor = successors[0]
    assert successor['payload']['targetfiles'] == (
        ['src.py'] if retry_kind == 'defer' else ['src.py', 'other.py'])
    assert successor['payload']['feedback'] == feedback
    assert successor['payload']['target_head'] == head
    assert successor['payload']['accepted_spec_base'] == head
    if retry_kind == 'capacity':
        assert 'review does not fit' in successor['payload']['capacity_feedback']['reason']
    assert successor['payload']['source_cycle_id'] == 'cycle'
    assert successor['payload']['source_cycle_round'] == 1
    cycle = store.source_cycles(session)[0]
    assert cycle['correction_job_id'] == successor['id'] and cycle['failed_passes'] == 1
    advance()
    assert len([job for job in store.snapshot(session)['jobs']
                if job['payload'].get('kind') == 'generate_spec']) == 2
    assert store.source_cycles(session)[0]['failed_passes'] == 1
    claimed = store.claim_job(session, 'next-corrector', now=6)
    assert claimed and claimed['id'] == successor['id']


@pytest.mark.parametrize('finished_before_fence', [False, True])
@pytest.mark.parametrize('kind', ['review_final', 'review_spec'])
def test_foreign_inflight_review_is_preserved_and_held_during_correction(tmp_path, finished_before_fence, kind):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    repo = repo_fixture.__wrapped__(tmp_path)
    store = Store(tmp_path / 'held/state.db')
    session = store.new_session(str(repo), 'base', 'challenged', {}, 1, 1000)
    payload = {'kind': kind, 'target_head': 'foreign-head'}
    loop = 'scl' if kind == 'review_spec' else 'ivl'
    job_id = store.enqueue_job(session, loop, 'code-reviewer', 'foreign-review', 'input', payload)
    job = store.claim_job(session, 'foreign-worker', now=2)
    assert job and job['id'] == job_id
    queued_id = store.enqueue_job(session, loop, 'data-reviewer', 'queued-foreign', 'input', payload)
    peer = store.claim_job(session, 'peer-worker', now=2) if kind == 'review_spec' else None
    if peer:
        assert peer['id'] == queued_id
    outcome = {'status': 'succeeded', 'result': {'decision': 'accept', 'summary': 'actual result'}}
    if finished_before_fence:
        store.finish_job(job_id, job['lease_token'], outcome, now=3)
        if peer:
            store.finish_job(peer['id'], peer['lease_token'], outcome, now=3)
    store.register_source_pass(session, 'cycle', 'challenged-item', 'challenged', 'base',
                               'plan', 'check', ['batch'], continuing=False)
    store.block_source_cycle(session, 'cycle', 'challenged', 'source correction unresolved')
    if not finished_before_fence:
        store.finish_job(job_id, job['lease_token'], outcome, now=4)
        if peer:
            store.finish_job(peer['id'], peer['lease_token'], outcome, now=4)
    saved = next(row for row in store.snapshot(session)['jobs'] if row['id'] == job_id)
    assert saved['outcome'] == outcome and saved['status'] == 'succeeded'
    assert store.transition_status(job_id) == 'pending'
    coordinator = Coordinator(repo, store, Agent(), None, None)
    coordinator.session_id = session
    coordinator._drain_transitions()
    assert store.transition_status(job_id) == 'pending'
    if peer:
        assert store.transition_status(peer['id']) == 'pending'
    assert not any(event['kind'] == 'fas_reviewed' for event in store.snapshot(session)['events'])
    early = store.enqueue_job(session, 'iil', 'debugger', 'independent-investigation', 'early',
                              {'kind': 'investigate_defect', 'target_head': 'foreign-head'})
    claimed = store.claim_job(session, 'independent-worker', now=5)
    assert claimed and claimed['id'] == early
    if not peer:
        queued = next(row for row in store.snapshot(session)['jobs'] if row['id'] == queued_id)
        assert queued['status'] == 'queued'
    assert store.transition_status(job_id) == 'pending'


def test_unknown_remote_outcome_checkpoints_pending_source_revision(tmp_path, monkeypatch):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    repo = repo_fixture.__wrapped__(tmp_path)
    head = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'remote-pending/state.db')
    session = store.new_session(str(repo), head, head, {}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'item', head, head,
                               'plan', 'check', ['batch'], continuing=False)
    payload = _source_job_payload(head, 'plan', 'check')
    job_id = store.enqueue_job(session, 'ivl', 'code-reviewer', 'source', 'input', payload)
    store.enqueue_job(session, 'ivl', 'data-reviewer', 'peer', 'peer-input', payload)
    job = store.claim_job(session, 'worker', now=2)
    assert job and job['id'] == job_id
    result = _review_result()
    outcome, decision = _source_outcome_and_decision(job_id, head, 'check', 'reviewer', 'decision', result)
    store.finish_job(job_id, job['lease_token'], outcome, now=3)
    store.begin_remote_effect(session, 'publication_started', {'head': 'foreign-head'})
    coordinator = Coordinator(repo, store, Agent(), None, None)
    coordinator.session_id = session
    # This isolates transition error handling; the real workflow separately exercises manifest validation.
    monkeypatch.setattr(coordinator, '_advance', lambda *_: store.record_source_decision(session, 'cycle', decision))
    coordinator._drain_transitions()
    assert coordinator.stop.is_set()
    assert store.transition_status(job_id) == 'pending'
    assert not store.source_head_fenced(session, head)
    snapshot = store.snapshot(session)
    assert not any(row['id'] == 'decision' for row in snapshot['decisions'])
    assert any(event['kind'] == 'remote_reconciliation_pending' for event in snapshot['events'])


def test_scl_revision_pair_survives_reopen_lease_expiry_and_atomic_replay(tmp_path):
    from mentat.autonomy_state import Store

    path = tmp_path / 'pair-resume/state.db'
    store = Store(path)
    session = store.new_session(str(tmp_path / 'repo'), 'base', 'h0', {'max_attempts': 3}, 1, 1000)
    store.register_source_pass(session, 'cycle', 'item', 'h0', 'base', 'plan', 'check', ['batch'],
                               continuing=False)
    for index, role in enumerate(('code-reviewer', 'data-reviewer')):
        store.enqueue_job(session, 'ivl', role, f'source-{index}', f'source-input-{index}',
                          _source_job_payload('h0', 'plan', 'check'))
    feedback = []
    for index in range(2):
        job = store.claim_job(session, f'source-worker-{index}', now=2)
        result = _review_result() if index == 0 else _review_result(decision='accept', corrections=[])
        outcome, decision = _source_outcome_and_decision(
            job['id'], 'h0', 'check', f'source-reviewer-{index}', f'source-decision-{index}', result)
        store.finish_job(job['id'], job['lease_token'], outcome, now=3)
        store.record_source_decision(session, 'cycle', decision)
        store.finish_transition(job['id'])
        feedback.append({'job_id': job['id'], 'decision_id': decision['id'], 'result': result})
    correction = {'kind': 'generate_spec', 'target_head': 'h0', 'accepted_spec_base': 'h0',
                  'review_base': 'base', 'source_cycle_id': 'cycle', 'source_cycle_round': 1,
                  'feedback': feedback}
    generator_id = store.finalize_source_correction(session, 'cycle', 'h0', feedback,
                                                     'corrector', 'input', correction,
                                                     actionable=True, max_attempts=3)
    generator = store.claim_job(session, 'generator', now=4)
    assert generator and generator['id'] == generator_id
    store.finish_job(generator_id, generator['lease_token'], {
        'status': 'succeeded', 'head': 'h1', 'author_id': 'generator-author',
        'result': {'decision': 'accept'}}, now=5)
    store.finish_transition(generator_id)
    proposal = {'head': 'h1', 'diff_digest': 'diff-h1', 'author_id': 'generator-author',
                'prior_authors': [], 'round': 0}
    review_payload = {'kind': 'review_spec', 'source_cycle_id': 'cycle', 'source_cycle_round': 1,
                      'target_head': 'h1', 'review_cohort': 'cohort', 'proposal': proposal}
    review_payload['source_epoch'] = store.source_finalization_epoch(session, review_payload)
    ids = [store.enqueue_job(session, 'scl', 'code-reviewer', f'review-{index}', f'input-{index}',
                             review_payload) for index in range(2)]
    first = store.claim_job(session, 'reviewer-one', now=6)
    second = store.claim_job(session, 'reviewer-two', now=6, lease_seconds=2)
    assert first and second and {first['id'], second['id']} == set(ids)
    results = [{'decision': 'revise', 'summary': 'Repair the note', 'findings': [{'summary': 'note'}]},
               {'decision': 'accept', 'summary': 'Scope is supported', 'findings': []}]
    def finish_review(job, index, now):
        outcome = {'status': 'succeeded', 'head': 'h1', 'diff_digest': 'diff-h1',
                   'author_id': f'scl-reviewer-{index}', 'result': results[index]}
        store.finish_job(job['id'], job['lease_token'], outcome, now=now)
        decision = {'id': f'scl-decision-{index}', 'job_id': job['id'], 'review_kind': 'review_spec',
                    'review_cohort': 'cohort', 'reviewer_id': f'scl-reviewer-{index}',
                    'head': 'h1', 'diff_digest': 'diff-h1', **results[index]}
        store.put_record(session, 'decision', decision)
        return outcome, decision
    first_outcome, first_decision = finish_review(first, 0, 7)
    assert store.source_scl_pair(session, 'cycle', 'cohort') is None
    store = Store(path)
    assert store.source_scl_pair(session, 'cycle', 'cohort') is None
    assert store.recover(session, now=9) == 1
    with pytest.raises(ValueError):
        store.finish_job(second['id'], second['lease_token'], {'status': 'succeeded'}, now=9)
    second_retry = store.claim_job(session, 'reviewer-two-resumed', now=10)
    assert second_retry and second_retry['id'] == second['id'] and second_retry['attempt'] == 2
    second_outcome, second_decision = finish_review(second_retry, 1, 11)
    pair = store.source_scl_pair(session, 'cycle', 'cohort')
    assert pair and [entry['job_id'] for entry in pair] == sorted(ids)
    assert {entry['decision_id'] for entry in pair} == {first_decision['id'], second_decision['id']}
    assert {json.dumps(entry['result'], sort_keys=True) for entry in pair} == {
        json.dumps(first_outcome['result'], sort_keys=True), json.dumps(second_outcome['result'], sort_keys=True)}
    successor = {**correction, 'target_head': 'h1', 'round': 1, 'scl_review_feedback': pair,
                 'targetfiles': ['a.py', 'b.py'], 'chunk': 0}
    successor_id = store.handoff_source_correction(session, 'cycle', first['id'], 'next', 'next-input', successor)
    assert store.handoff_source_correction(session, 'cycle', first['id'], 'next', 'next-input', successor) == successor_id
    assert store.source_scl_pair(session, 'cycle', 'cohort') == []
    with pytest.raises(ValueError):
        store.handoff_source_correction(session, 'cycle', second['id'], 'other', 'other-input', successor)
    continued = store.claim_job(session, 'continued-generator', now=12)
    assert continued and continued['id'] == successor_id
    store.finish_job(successor_id, continued['lease_token'], {
        'status': 'succeeded', 'head': 'h1', 'context_pending': ['a.py'],
        'result': {'decision': 'defer', 'summary': 'smaller frontier'}}, now=13)
    frontier = {**successor, 'targetfiles': ['a.py'], 'chunk': 1}
    frontier_id = store.handoff_source_correction(session, 'cycle', successor_id,
                                                 'frontier', 'frontier-input', frontier)
    snapshot = Store(path).snapshot(session)
    assert len([job for job in snapshot['jobs'] if job['payload'].get('kind') == 'generate_spec']) == 3
    frontier_job = next(job for job in snapshot['jobs'] if job['id'] == frontier_id)
    assert frontier_job['payload']['feedback'] == feedback
    assert frontier_job['payload']['scl_review_feedback'] == pair
    assert Store(path).source_cycles(session)[0]['failed_passes'] == 1
