"""Counterexample cases are protected negative controls, not positive proofs."""

import json
import subprocess
import sys
import time
import uuid
from copy import deepcopy

import pytest

from mentat.autonomy import Coordinator, default_config, state_path, validate_config
from mentat.autonomy_policy import check_policy, load_policy_path, pin_policy
from mentat.autonomy_state import Store


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def descriptor():
    return {'path': 'subject.py', 'function': 'clamp', 'inputs': {'x': 'int'},
            'requires': 'True', 'ensures': '0 <= result <= 10',
            'requirement_id': str(uuid.uuid4()),
            'counterexample_cases': [{'path': 'subject.py', 'function': 'bad_clamp'}]}


@pytest.fixture
def case_config(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    config = default_config(repo)
    config['source_checks'] = [descriptor()]
    return repo, config


def test_v2_policy_pins_ordered_cases_and_descriptors_without_aliases(case_config, tmp_path):
    repo, config = case_config
    first = deepcopy(config['source_checks'][0])
    second = deepcopy(first)
    second['function'] = 'another_clamp'
    second['requirement_id'] = str(uuid.uuid4())
    second['counterexample_cases'].append({'path': 'other.py', 'function': 'bad'})
    config['source_checks'].append(second)
    policy = pin_policy(repo, tmp_path / 'state', config)
    assert policy['schema_version'] == 2
    assert policy['mandatory_source_checks'] == [first, second]
    path = state_path(repo, tmp_path / 'state')
    assert load_policy_path(path)['mandatory_source_checks'] == [first, second]
    config['source_checks'][0]['counterexample_cases'][0]['function'] = 'mutated'
    assert policy['mandatory_source_checks'] == [first, second]
    with pytest.raises(ValueError):
        check_policy(config, policy)
    valid = deepcopy(config)
    valid['source_checks'] = [first, second, {**deepcopy(first), 'function': 'extra'}]
    check_policy(valid, policy)
    for mutation in ('drop_case', 'drop_check', 'reorder', 'contract', 'identity', 'reorder_cases'):
        changed = deepcopy(valid)
        if mutation == 'drop_case':
            changed['source_checks'][0]['counterexample_cases'] = []
        elif mutation == 'drop_check':
            changed['source_checks'].pop(0)
        elif mutation == 'reorder':
            changed['source_checks'][0], changed['source_checks'][1] = changed['source_checks'][1], changed['source_checks'][0]
        elif mutation == 'contract':
            changed['source_checks'][0]['ensures'] = 'True'
        elif mutation == 'identity':
            changed['source_checks'][0]['requirement_id'] = str(uuid.uuid4())
        else:
            changed['source_checks'][1]['counterexample_cases'].reverse()
        with pytest.raises(ValueError):
            check_policy(changed, policy)


def test_v1_policy_rejects_new_cases_without_rewriting_authority(case_config, tmp_path):
    repo, case_config_value = case_config
    plain = deepcopy(case_config_value)
    plain['source_checks'][0].pop('counterexample_cases')
    legacy = {'schema_version': 1, 'repo': str(repo.resolve()),
              'mandatory_local_checks': [], 'mandatory_worker_checks': [],
              'model': plain['model'], 'reasoning_effort': plain['reasoning_effort'],
              'auto_merge': False, 'session_seconds': 7200, 'concurrency': 3, 'max_attempts': 3}
    policy_path = state_path(repo, tmp_path / 'state').parent / 'policy.json'
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps(legacy))
    before = policy_path.read_bytes()
    assert load_policy_path(policy_path.parent / 'state.db') == legacy
    check_policy(plain, legacy)
    with pytest.raises(ValueError):
        check_policy(case_config_value, legacy)
    with pytest.raises(ValueError):
        pin_policy(repo, tmp_path / 'state', case_config_value)
    assert policy_path.read_bytes() == before


@pytest.mark.parametrize('fault', ['unknown', 'missing_path', 'missing_function', 'unsafe',
                                   'duplicate', 'too_many', 'invalid_collection'])
def test_case_descriptors_reject_invalid_or_unbounded_input(case_config, fault):
    _, config = case_config
    cases = config['source_checks'][0]['counterexample_cases']
    if fault == 'unknown':
        cases[0]['expect'] = 'pass'
    elif fault == 'missing_path':
        del cases[0]['path']
    elif fault == 'missing_function':
        del cases[0]['function']
    elif fault == 'unsafe':
        cases[0]['path'] = '../outside.py'
    elif fault == 'duplicate':
        cases.append(deepcopy(cases[0]))
    elif fault == 'too_many':
        cases.extend({'path': f'mutant{i}.py', 'function': 'bad'} for i in range(8))
    else:
        config['source_checks'][0]['counterexample_cases'] = 'subject.py'
    with pytest.raises(ValueError):
        validate_config(config)


class ReplayWorker:
    def __init__(self, fault=None):
        self.fault = fault
        self.replays = []

    def cancel_all(self):
        pass

    def diagnostics(self):
        return {'status': 'ready'}

    def run(self, repo, rev, argv, *, timeout_seconds):
        return {'status': 'succeeded', 'returncode': 0, 'revision': rev, 'tools': {'python': 'test'}}

    def reproduce(self, repo, rev, evidence, *, timeout_seconds):
        self.replays.append(deepcopy(evidence))
        if self.fault == 'exception':
            raise TimeoutError('worker timeout')
        witness = deepcopy(evidence['counterexample'])
        result = {'status': 'passed', 'revision': rev, 'evidence_id': evidence['id'],
                  'inputs': witness, 'witness': deepcopy(witness),
                  'source_sha256': evidence['source_identity']['source_sha256'],
                  'precondition': True, 'violated': True, 'postcondition': False, 'result': -1,
                  'method': 'protected-runtime-reproduction', 'interpreter': '3.12.16 (isolated)',
                  'tools': {'selected_python_version': 'Python 3.12.16'}}
        changes = {'wrong_head': ('revision', '0' * 40), 'wrong_id': ('evidence_id', 'other'),
                   'wrong_source': ('source_sha256', '0' * 64), 'wrong_inputs': ('inputs', {'x': 1}),
                   'wrong_witness': ('witness', {'x': 1}), 'bool_integer': ('inputs', {'x': False}),
                   'wrong_precondition': ('precondition', False), 'wrong_violation': ('violated', False),
                   'wrong_postcondition': ('postcondition', True), 'wrong_method': ('method', 'test'),
                   'missing_tools': ('tools', {}), 'wrong_python': ('interpreter', '3.14.0 (isolated)'),
                   'vm_error': ('status', 'incomplete')}
        if self.fault in changes:
            key, value = changes[self.fault]
            result[key] = value
        return result


@pytest.fixture
def checked_case_repo(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'user.email', 'test@example.com')
    (repo / 'subject.py').write_text('def clamp(x):\n    if x < 0:\n        return 0\n    if x > 10:\n        return 10\n    return x\n')
    (repo / 'bad.py').write_text('def bad_clamp(x):\n    return -1\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'source cases')
    head = git(repo, 'rev-parse', 'HEAD')
    config = default_config(repo)
    config['local_checks'] = [['check']]
    config['source_checks'] = [{'path': 'subject.py', 'function': 'clamp',
        'inputs': {'x': 'int'}, 'requires': 'x == 0', 'ensures': '0 <= result <= 10',
        'counterexample_cases': [{'path': 'bad.py', 'function': 'bad_clamp'}]}]
    return repo, head, config


def execute_check(repo, head, config, tmp_path, worker):
    store = Store(tmp_path / 'state/state.db')
    coordinator = Coordinator(repo, store, object(), worker, object())
    coordinator.config = config
    from mentat.autonomy import current_tool_identity
    coordinator.tool_identity = current_tool_identity()
    now = time.time()
    coordinator.session_id = store.new_session(str(repo), head, head, config, now, now + 90)
    job = {'id': 'verify', 'loop': 'ivl', 'item_id': 'verify', 'attempt': 1,
           'payload': {'kind': 'pre_review_checks', 'target_head': head, 'commands': [],
                       'source_checks': [], 'check_key': 'key'}}
    return coordinator, coordinator._execute(job, now + 60)['evidence']


def test_real_solver_counterexample_is_replayed_as_separate_negative_evidence(checked_case_repo, tmp_path):
    repo, head, config = checked_case_repo
    worker = ReplayWorker()
    _, evidence = execute_check(repo, head, config, tmp_path, worker)
    assert evidence['status'] == 'passed', (evidence.get('source'), evidence.get('source_cases'))
    assert len(evidence['source']) == 1 and evidence['source'][0]['status'] == 'proved'
    assert len(evidence['source_cases']) == 1 and len(worker.replays) == 1
    case = evidence['source_cases'][0]
    assert case['status'] == 'passed'
    proof = case['evidence']
    assert proof['id'] and proof['status'] == 'counterexample'
    assert proof['source_identity']['revision'] == head
    assert proof['source_identity']['path'] == 'bad.py'
    assert proof['source_identity']['function'] == 'bad_clamp'
    assert proof['contract'] == {'inputs': {'x': 'int'}, 'requires': 'x == 0',
                                 'ensures': '0 <= result <= 10'}
    assert proof['counterexample'] == {'x': 0}
    assert worker.replays[0] == proof
    assert case['reproduction']['evidence_id'] == proof['id']
    assert case['reproduction']['inputs'] == proof['counterexample']
    assert proof not in evidence['source']


@pytest.mark.parametrize('fault', ['wrong_head', 'wrong_id', 'wrong_source', 'wrong_inputs',
    'wrong_witness', 'bool_integer', 'wrong_precondition', 'wrong_violation',
    'wrong_postcondition', 'wrong_method', 'missing_tools', 'wrong_python', 'vm_error', 'exception'])
def test_case_replay_mismatch_never_passes_acceptance(checked_case_repo, tmp_path, fault):
    repo, head, config = checked_case_repo
    worker = ReplayWorker(fault)
    try:
        _, evidence = execute_check(repo, head, config, tmp_path, worker)
    except (ValueError, TimeoutError, RuntimeError):
        # Explicit controller failure also rejects this evidence.
        assert worker.replays
        return
    assert len(worker.replays) == 1
    assert evidence['status'] != 'passed'
    assert all(case['status'] != 'passed' for case in evidence['source_cases'])


@pytest.mark.parametrize('fault', ['proved', 'unsupported', 'missing'])
def test_unsuitable_mutant_never_reaches_replay(checked_case_repo, tmp_path, fault):
    repo, _, config = checked_case_repo
    path = repo / 'bad.py'
    if fault == 'proved':
        path.write_text('def bad_clamp(x):\n    return 0\n')
    elif fault == 'unsupported':
        path.write_text('def bad_clamp(x):\n    return x * x\n')
    else:
        path.unlink()
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'invalid control')
    head = git(repo, 'rev-parse', 'HEAD')
    worker = ReplayWorker()
    _, evidence = execute_check(repo, head, config, tmp_path, worker)
    assert evidence['status'] != 'passed'
    assert not worker.replays
    assert len(evidence['source_cases']) == 1
    assert evidence['source_cases'][0]['status'] != 'passed'


def test_nullary_counterexample_replays_empty_typed_witness(checked_case_repo, tmp_path):
    repo, _, config = checked_case_repo
    (repo / 'subject.py').write_text('def clamp():\n    return 0\n')
    (repo / 'bad.py').write_text('def bad_clamp():\n    return -1\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'nullary cases')
    head = git(repo, 'rev-parse', 'HEAD')
    config['source_checks'][0].update(inputs={}, requires='True')
    worker = ReplayWorker()
    _, evidence = execute_check(repo, head, config, tmp_path, worker)
    assert evidence['status'] == 'passed'
    assert len(worker.replays) == 1 and worker.replays[0]['counterexample'] == {}
    assert evidence['source_cases'][0]['reproduction']['inputs'] == {}


class RecordingAgent:
    def __init__(self, worker):
        self.worker = worker
        self.calls = []

    def cancel_all(self):
        pass

    def run(self, repo, job, output, *, timeout_seconds):
        from mentat.autonomy import AREAS

        payload = job['payload']
        kind = payload['kind']
        context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
        self.calls.append((kind, payload, context, len(self.worker.replays)))
        if kind == 'generate_spec':
            path = repo / 'openspec/specs/app/spec.md'
            path.parent.mkdir(parents=True, exist_ok=True)
            requirements = [{'id': str(uuid.uuid4()), 'area': area, 'origin': 'uncertain',
                'statement': 'Unspecified', 'spec_path': 'openspec/specs/app/spec.md'} for area in AREAS]
            path.write_text('\n'.join(row['id'] for row in requirements))
            (repo / '.mentat/fas.json').write_text(json.dumps({'schema_version': 1,
                'requirements': requirements, 'claims': [], 'surfaces': [
                    {'path': row['path'], 'status': 'gap', 'requirement_ids': []}
                    for row in payload['surfaces']]}))
        return {'status': 'succeeded', 'session_id': job['id'], 'result': {
            'decision': 'accept', 'summary': json.dumps({
                'schema_version': 2,
                'findings': 'Inspected the supplied source and its mapped requirements.',
                'unresolved_dependencies': [], 'spec_corrections': [],
            }) if kind == 'review_final_source' else 'reviewed', 'findings': [],
            'verification_requests': [], 'requirements': [], 'claims': [],
            'defects': [], 'changes': []}}


class RecordingGitHub:
    def __init__(self):
        self.published = []
        self.merged = []

    def publish(self, repo, branch, title, body_file):
        self.published.append(git(repo, 'rev-parse', 'HEAD'))
        return {'status': 'succeeded', 'url': 'https://example.test/pull/1'}

    def merge(self, repo, number, expected_head, expected_base, **kwargs):
        self.merged.append(expected_head)
        return {'status': 'succeeded'}


def run_case_workflow(repo, config, tmp_path, worker, agent_class=RecordingAgent):
    (repo / '.mentat').mkdir(exist_ok=True)
    (repo / '.mentat/workspace.json').write_text(json.dumps(config))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'workspace policy')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    store = Store(tmp_path / 'workflow/state.db')
    policy = {'schema_version': 2, 'repo': str(repo.resolve()),
              'mandatory_local_checks': [['check']], 'mandatory_worker_checks': [],
              'mandatory_source_checks': deepcopy(config['source_checks']),
              'model': config['model'], 'reasoning_effort': config['reasoning_effort'],
              'auto_merge': True, 'session_seconds': 7200, 'concurrency': 3, 'max_attempts': 3}
    (store.path.parent / 'policy.json').write_text(json.dumps(policy))
    agent, github = agent_class(worker), RecordingGitHub()
    coordinator = Coordinator(repo, store, agent, worker, github)
    return coordinator.run(), agent, github


def test_case_plan_reaches_generator_and_fresh_evidence_reaches_both_review_stages(checked_case_repo, tmp_path):
    repo, _, config = checked_case_repo
    config['auto_merge'] = True
    worker = ReplayWorker()
    snapshot, agent, github = run_case_workflow(repo, config, tmp_path, worker)
    assert github.published, snapshot
    generated = [entry for entry in agent.calls if entry[0] == 'generate_verification']
    assert len(generated) == 1
    _, _, generator, replay_count = generated[0]
    assert replay_count == 0
    assert generator['files']['subject.py'] == (repo / 'subject.py').read_text()
    assert generator['files']['bad.py'] == (repo / 'bad.py').read_text()
    serialized = json.dumps(generator).lower()
    assert 'pending' in serialized or 'not executed' in serialized
    assert 'bad_clamp' in serialized and '0 <= result <= 10' in serialized
    for kind in ('review_verification', 'review_final'):
        pair = [(payload, context) for stage, payload, context, _ in agent.calls if stage == kind]
        assert len(pair) == 2
        for payload, context in pair:
            assert context['files']['subject.py'] == (repo / 'subject.py').read_text()
            assert context['files']['bad.py'] == (repo / 'bad.py').read_text()
            assert context['trusted_source_verifier']['source']
            summary = payload['check_summary']
            assert summary['head'] == context['revision'] == github.published[0]
            assert len(summary['source_cases']) == 1
            case = summary['source_cases'][0]
            assert case['status'] == 'passed'
            assert case['evidence']['source_identity']['revision'] == context['revision']
            assert case['reproduction']['evidence_id'] == case['evidence']['id']
    assert not any(j['payload']['kind'] in {'adjudicate_defect', 'investigate_defect', 'reproduce_defect'}
                   for j in snapshot['jobs'])


def test_failed_case_cannot_reach_independent_acceptance_or_iil(checked_case_repo, tmp_path):
    repo, _, config = checked_case_repo
    config['auto_merge'] = True
    worker = ReplayWorker('wrong_id')
    snapshot, agent, github = run_case_workflow(repo, config, tmp_path, worker)
    assert worker.replays
    assert not github.published and not github.merged
    assert not any(kind in {'review_verification', 'review_final'} for kind, *_ in agent.calls)
    assert not any(j['payload']['kind'] in {'adjudicate_defect', 'investigate_defect', 'reproduce_defect'}
                   for j in snapshot['jobs'])


@pytest.mark.parametrize('fault', ['wrong_head', 'wrong_path', 'wrong_function', 'wrong_blob',
    'wrong_source_hash', 'wrong_contract', 'missing_witness', 'wrong_witness_type',
    'missing_tool_identity', 'wrong_solver_method'])
def test_untrusted_solver_result_cannot_trigger_case_replay(checked_case_repo, tmp_path, monkeypatch, fault):
    from mentat import source_verify

    repo, head, config = checked_case_repo
    verify = source_verify.verify_source

    def altered_source_result(repo, rev, **check):
        result = verify(repo, rev, **check)
        if check['path'] != 'bad.py':
            return result
        if fault == 'wrong_head':
            result['source_identity']['revision'] = '0' * 40
        elif fault == 'wrong_path':
            result['source_identity']['path'] = 'subject.py'
        elif fault == 'wrong_function':
            result['source_identity']['function'] = 'other'
        elif fault == 'wrong_blob':
            result['source_identity']['blob_oid'] = '0' * 40
        elif fault == 'wrong_source_hash':
            result['source_identity']['source_sha256'] = '0' * 64
        elif fault == 'wrong_contract':
            result['contract']['ensures'] = 'True'
        elif fault == 'missing_witness':
            result.pop('counterexample')
        elif fault == 'wrong_witness_type':
            result['counterexample'] = {'x': False}
        elif fault == 'missing_tool_identity':
            result.pop('frontend_sha256')
        else:
            result['method'] = 'external-untrusted'
        return result

    monkeypatch.setattr(source_verify, 'verify_source', altered_source_result)
    worker = ReplayWorker()
    try:
        _, evidence = execute_check(repo, head, config, tmp_path, worker)
    except (ValueError, RuntimeError):
        assert not worker.replays
        return
    assert evidence['status'] != 'passed'
    assert not worker.replays


def test_new_policy_enforces_mentat_minimum_and_bounds_before_creation(tmp_path):
    repo = tmp_path / 'mentat'
    (repo / 'src/mentat').mkdir(parents=True)
    (repo / 'scripts').mkdir()
    (repo / 'src/mentat/proof_checker.py').touch()
    (repo / 'scripts/verify').touch()
    config = default_config(repo)
    minimum = deepcopy(config['source_checks'])
    assert minimum and minimum[0]['counterexample_cases']
    policy = pin_policy(repo, tmp_path / 'state', config)
    assert policy['mandatory_source_checks'] == minimum
    weak = deepcopy(minimum[0])
    weak['ensures'] = 'True'
    extra = deepcopy(config)
    extra['source_checks'].insert(0, weak)
    extra_policy = pin_policy(repo, tmp_path / 'extra-state', extra)
    assert extra_policy['mandatory_source_checks'] == [weak, *minimum]
    extra['source_checks'].pop(1)
    with pytest.raises(ValueError):
        check_policy(extra, extra_policy)
    with pytest.raises(ValueError):
        pin_policy(repo, tmp_path / 'weak-state', extra)
    changed = deepcopy(config)
    changed['source_checks'][0]['counterexample_cases'] = []
    with pytest.raises(ValueError):
        check_policy(changed, policy)
    with pytest.raises(ValueError):
        pin_policy(repo, tmp_path / 'new-state', changed)
    assert not (state_path(repo, tmp_path / 'new-state').parent / 'policy.json').exists()

    other = tmp_path / 'other'
    other.mkdir()
    huge = default_config(other)
    huge['source_checks'] = [
        {'path': 'subject.py', 'function': f'f{i}', 'inputs': {}, 'requires': 'True',
         'ensures': 'x' * 4000} for i in range(20)]
    validate_config(huge)
    with pytest.raises(ValueError, match='64|size|large'):
        pin_policy(other, tmp_path / 'large-state', huge)
    assert not (state_path(other, tmp_path / 'large-state').parent / 'policy.json').exists()


def test_all_case_count_limit_is_enforced(case_config):
    _, config = case_config
    config['source_checks'] = [
        {'path': 'subject.py', 'function': f'f{i}', 'inputs': {}, 'requires': 'True',
         'ensures': 'True', 'counterexample_cases': [
             {'path': f'bad{i}_{j}.py', 'function': 'f'} for j in range(8)]}
        for i in range(17)]
    boundary = deepcopy(config)
    boundary['source_checks'] = boundary['source_checks'][:16]
    validate_config(boundary)  # Exactly 128 cases remains within the bound.
    with pytest.raises(ValueError):
        validate_config(config)


def test_ordered_cases_and_candidate_head_change_check_identity(checked_case_repo, tmp_path):
    repo, _, config = checked_case_repo
    path = repo / 'bad.py'
    path.write_text(path.read_text() + '\ndef second_bad(x):\n    return -2\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'second mutant')
    head = git(repo, 'rev-parse', 'HEAD')
    config['source_checks'][0]['counterexample_cases'].append({'path': 'bad.py', 'function': 'second_bad'})
    worker = ReplayWorker()
    coordinator, evidence = execute_check(repo, head, config, tmp_path, worker)
    assert evidence['status'] == 'passed'
    assert [case['case']['function'] for case in evidence['source_cases']] == ['bad_clamp', 'second_bad']
    identities = [case['evidence']['id'] for case in evidence['source_cases']]
    assert len(set(identities)) == 2
    original_key = coordinator._check_key(head, [], [], None)
    reordered = deepcopy(config['source_checks'])
    reordered[0]['counterexample_cases'].reverse()
    coordinator.config = {**config, 'source_checks': reordered}
    assert coordinator._check_key(head, [], [], None) != original_key
    coordinator.config = config
    path.write_text(path.read_text() + '\n# newer source\n')
    git(repo, 'commit', '-qam', 'new candidate')
    newer = git(repo, 'rev-parse', 'HEAD')
    assert coordinator._check_key(newer, [], [], None) != original_key


def test_generator_does_not_reuse_stale_case_evidence(checked_case_repo, tmp_path):
    repo, head, config = checked_case_repo
    worker = ReplayWorker()
    coordinator, evidence = execute_check(repo, head, config, tmp_path, worker)
    payload = {'kind': 'generate_verification', 'feedback': {'check_summary': {
        'head': '0' * 40, 'source_cases': evidence['source_cases']}}}
    text, _, _, _ = coordinator._source_context(head, {'payload': payload})
    context = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert context['source_cases']['status'] in {'pending', 'stale'}
    assert not context['source_cases']['cases']
    assert context['files']['bad.py'] == (repo / 'bad.py').read_text()
    payload['feedback']['check_summary']['head'] = head
    text, _, _, _ = coordinator._source_context(head, {'payload': payload})
    context = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert context['source_cases']['status'] == 'fresh'
    assert context['source_cases']['cases'] == evidence['source_cases']


def test_generator_fails_closed_when_case_source_exceeds_context_capacity(checked_case_repo, tmp_path):
    from mentat.autonomy import ReviewContextCapacity, current_tool_identity

    repo, _, config = checked_case_repo
    (repo / 'bad.py').write_text('def bad_clamp(x):\n    return -1\n# ' + 'x' * 240_001)
    git(repo, 'commit', '-qam', 'oversized mandatory case')
    head = git(repo, 'rev-parse', 'HEAD')
    store = Store(tmp_path / 'capacity/state.db')
    coordinator = Coordinator(repo, store, object(), ReplayWorker(), object())
    coordinator.config = config
    coordinator.tool_identity = current_tool_identity()
    now = time.time()
    coordinator.session_id = store.new_session(str(repo), head, head, config, now, now + 90)
    with pytest.raises(ReviewContextCapacity):
        coordinator._source_context(head, {'payload': {'kind': 'generate_verification'}})


def test_case_evidence_is_complete_beyond_twelve(checked_case_repo, tmp_path):
    repo, _, config = checked_case_repo
    source = repo / 'subject.py'
    source.write_text(source.read_text() + '\ndef clamp2(x):\n    return 0\n')
    mutant = repo / 'bad.py'
    mutant.write_text('\n'.join(f'def bad{i}(x):\n    return -1\n' for i in range(13)))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'thirteen controls')
    head = git(repo, 'rev-parse', 'HEAD')
    first = deepcopy(config['source_checks'][0])
    first['counterexample_cases'] = [{'path': 'bad.py', 'function': f'bad{i}'} for i in range(8)]
    second = deepcopy(first)
    second['function'] = 'clamp2'
    second['counterexample_cases'] = [{'path': 'bad.py', 'function': f'bad{i}'} for i in range(8, 13)]
    config['source_checks'] = [first, second]
    worker = ReplayWorker()
    coordinator, evidence = execute_check(repo, head, config, tmp_path, worker)
    assert evidence['status'] == 'passed', {
        'source': [
            {key: str(row.get(key))[:1024] for key in ('status', 'reason', 'detail')}
            for row in evidence.get('source', [])
        ],
        'source_cases': [
            {'function': row.get('case', {}).get('function'), 'status': row.get('status'),
             'reason': str(row.get('evidence', {}).get('reason'))[:1024],
             'replay_reason': str(row.get('reproduction', {}).get('reason'))[:1024]}
            for row in evidence.get('source_cases', [])
        ],
    }
    assert len(evidence['source_cases']) == len(worker.replays) == 13
    summary = coordinator._check_summary({'id': 'check', 'head': head, 'base': head,
                                           'check_key': 'key', **evidence})
    assert len(summary['source_cases']) == 13
    assert [row['case']['function'] for row in summary['source_cases']] == [f'bad{i}' for i in range(13)]
    assert all(row['status'] == 'passed' for row in summary['source_cases'])


@pytest.mark.parametrize('feedback', [['review finding'], 'review finding',
    {'check_summary': None}, {'check_summary': ['malformed']}, {'check_summary': 'malformed'}])
def test_generation_context_accepts_non_dict_revision_feedback(checked_case_repo, tmp_path, feedback):
    repo, head, config = checked_case_repo
    coordinator, _ = execute_check(repo, head, config, tmp_path, ReplayWorker())
    text, _, _, _ = coordinator._source_context(head, {'payload': {
        'kind': 'generate_verification', 'feedback': feedback}})
    context = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert context['source_cases']['status'] == 'pending'
    assert context['files']['bad.py'] == (repo / 'bad.py').read_text()


def test_reviewer_revision_feedback_regenerates_verification_with_cases(checked_case_repo, tmp_path):
    repo, _, config = checked_case_repo
    config['auto_merge'] = True

    class RevisingAgent(RecordingAgent):
        def run(self, repo, job, output, *, timeout_seconds):
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            payload = job['payload']
            if payload['kind'] == 'review_verification' and not payload['proposal'].get('round'):
                result['result']['decision'] = 'revise'
                result['result']['findings'] = [{'summary': 'Clarify verification'}]
            return result

    snapshot, agent, github = run_case_workflow(repo, config, tmp_path, ReplayWorker(), RevisingAgent)
    generations = [entry for entry in agent.calls if entry[0] == 'generate_verification']
    assert len(generations) == 2, snapshot
    assert isinstance(generations[1][1]['feedback'], list)
    assert generations[1][2]['source_cases']['status'] == 'pending'
    assert github.published, snapshot


@pytest.mark.parametrize('phase', ['initial_stop', 'initial_deadline', 'positive_stop',
                                    'positive_deadline', 'negative_stop', 'negative_deadline'])
def test_case_checks_stop_at_cancellation_or_deadline(checked_case_repo, tmp_path, monkeypatch, phase):
    from mentat import source_verify
    from mentat.autonomy import current_tool_identity

    repo, head, config = checked_case_repo
    worker = ReplayWorker()
    store = Store(tmp_path / 'cancel/state.db')
    now = time.time()
    coordinator = Coordinator(repo, store, object(), worker, object())
    coordinator.config = config
    coordinator.tool_identity = current_tool_identity()
    coordinator.session_id = store.new_session(str(repo), head, head, config, now, now + 90)
    deadline = now + 60
    actual_verify = source_verify.verify_source
    calls = []

    def stopping_verify(repo, rev, **check):
        calls.append(check['path'])
        result = actual_verify(repo, rev, **check)
        if ((phase.startswith('positive') and check['path'] == 'subject.py') or
                (phase.startswith('negative') and check['path'] == 'bad.py')):
            if phase.endswith('stop'):
                coordinator.stop.set()
            else:
                coordinator.clock = lambda: deadline + 1
        return result

    monkeypatch.setattr(source_verify, 'verify_source', stopping_verify)
    if phase == 'initial_stop':
        coordinator.stop.set()
    elif phase == 'initial_deadline':
        coordinator.clock = lambda: deadline + 1
    outcome = coordinator._execute({'id': 'cancel', 'loop': 'ivl', 'item_id': 'cancel',
        'attempt': 1, 'payload': {'kind': 'pre_review_checks', 'target_head': head,
        'commands': [], 'source_checks': [], 'check_key': 'key'}}, deadline)
    assert outcome.get('status') == 'failed' or outcome.get('evidence', {}).get('status') != 'passed'
    assert calls == ([] if phase.startswith('initial') else
                     ['subject.py'] if phase.startswith('positive') else ['subject.py', 'bad.py'])
    assert not worker.replays


def test_v1_no_case_source_checks_allow_distinct_contracts_on_same_function(checked_case_repo, tmp_path):
    repo, head, config = checked_case_repo
    first = deepcopy(config['source_checks'][0])
    first.pop('counterexample_cases')
    first['claim_id'] = 'claim-one'
    second = {**deepcopy(first), 'claim_id': 'claim-two', 'requires': 'x == 1', 'ensures': 'result == 1'}
    config['source_checks'] = [first, second, deepcopy(first)]
    validate_config(config)
    legacy = {'schema_version': 1, 'repo': str(repo.resolve()), 'mandatory_local_checks': [],
              'mandatory_worker_checks': [], 'model': config['model'],
              'reasoning_effort': config['reasoning_effort'], 'auto_merge': False,
              'session_seconds': 7200, 'concurrency': 3, 'max_attempts': 3}
    check_policy(config, legacy)
    _, evidence = execute_check(repo, head, config, tmp_path, ReplayWorker())
    assert evidence['status'] == 'passed'
    assert [item['claim_id'] for item in evidence['source']] == ['claim-one', 'claim-two', 'claim-one']
    assert [item['contract']['requires'] for item in evidence['source']] == ['x == 0', 'x == 1', 'x == 0']
    assert evidence['source_cases'] == []


def test_legacy_no_case_timeout_is_forwarded_without_changing_logical_contract(checked_case_repo, tmp_path, monkeypatch):
    from mentat import source_verify

    repo, head, config = checked_case_repo
    config['source_checks'][0].pop('counterexample_cases')
    config['source_checks'][0]['timeout_ms'] = 750
    config['source_checks'][0]['claim_id'] = 'claim-one'
    validate_config(config)
    legacy = {'schema_version': 1, 'mandatory_local_checks': [], 'mandatory_worker_checks': [],
              'model': config['model'], 'reasoning_effort': config['reasoning_effort'],
              'auto_merge': False, 'session_seconds': 7200, 'concurrency': 3, 'max_attempts': 3}
    check_policy(config, legacy)
    verify = source_verify.verify_source
    calls = []

    def capture(repo, rev, **check):
        calls.append(deepcopy(check))
        # Check forwarding separately from the proof subprocess's wall time.
        return verify(repo, rev, **{**check, 'timeout_ms': 5000})

    monkeypatch.setattr(source_verify, 'verify_source', capture)
    _, evidence = execute_check(repo, head, config, tmp_path, ReplayWorker())
    assert evidence['status'] == 'passed', evidence['source']
    assert len(calls) == 1 and calls[0]['timeout_ms'] == 750
    assert evidence['source'][0]['status'] == 'proved'
    assert evidence['source'][0]['claim_id'] == 'claim-one'
    assert evidence['source'][0]['contract'] == {'inputs': {'x': 'int'}, 'requires': 'x == 0',
                                                'ensures': '0 <= result <= 10'}


@pytest.mark.parametrize(('remaining', 'expected'), [(60, 750), (0.5, 500)])
def test_case_solver_timeout_inherits_descriptor_and_remaining_budget(checked_case_repo, tmp_path, monkeypatch, remaining, expected):
    from mentat import source_verify
    from mentat.autonomy import current_tool_identity

    repo, head, config = checked_case_repo
    config['source_checks'][0]['timeout_ms'] = 750
    validate_config(config)
    store = Store(tmp_path / 'timed/state.db')
    worker = ReplayWorker()
    coordinator = Coordinator(repo, store, object(), worker, object(), clock=lambda: 100)
    coordinator.config = config
    coordinator.tool_identity = current_tool_identity()
    coordinator.session_id = store.new_session(str(repo), head, head, config, 1, 1000)
    verify = source_verify.verify_source
    calls = []

    def capture(repo, rev, **check):
        calls.append(deepcopy(check))
        return verify(repo, rev, **{**check, 'timeout_ms': 5000})

    monkeypatch.setattr(source_verify, 'verify_source', capture)
    result = coordinator._execute({'id': 'timed', 'loop': 'ivl', 'item_id': 'timed',
        'attempt': 1, 'payload': {'kind': 'pre_review_checks', 'target_head': head,
        'commands': [], 'source_checks': [], 'check_key': 'key'}}, 100 + remaining)
    assert result['evidence']['status'] == 'passed', (result['evidence']['source'], result['evidence']['source_cases'])
    assert [call['path'] for call in calls] == ['subject.py', 'bad.py']
    assert [call['timeout_ms'] for call in calls] == [expected, expected]
    assert len(worker.replays) == 1
    assert 'timeout_ms' not in worker.replays[0]['contract']


def test_source_worker_deadline_leaves_proof_incomplete(checked_case_repo, monkeypatch):
    from mentat import source_verify

    repo, head, _ = checked_case_repo
    run = source_verify.process.run

    def deadline(argv, **kwargs):
        if argv[0] == sys.executable:
            raise TimeoutError('source worker deadline')
        return run(argv, **kwargs)

    monkeypatch.setattr(source_verify.process, 'run', deadline)
    evidence = source_verify.verify_source(
        repo, head, 'subject.py', 'clamp', inputs={'x': 'int'},
        requires='x == 0', ensures='0 <= result <= 10', timeout_ms=500,
    )
    assert evidence['status'] == 'incomplete'
    assert evidence['reason'] == 'source worker deadline'


@pytest.mark.parametrize('timeout', [0, True, 30001])
def test_invalid_timeout_cannot_produce_proof_or_case_approval(checked_case_repo, tmp_path, timeout):
    repo, head, config = checked_case_repo
    config['source_checks'][0]['timeout_ms'] = timeout
    with pytest.raises(ValueError):
        validate_config(config)
    worker = ReplayWorker()
    try:
        _, evidence = execute_check(repo, head, config, tmp_path, worker)
    except (ValueError, TypeError):
        assert not worker.replays
        return
    assert evidence['status'] != 'passed'
    assert not worker.replays


def test_timeout_is_pinned_but_not_part_of_mentat_semantic_minimum(tmp_path):
    repo = tmp_path / 'mentat'
    (repo / 'src/mentat').mkdir(parents=True)
    (repo / 'scripts').mkdir()
    (repo / 'src/mentat/proof_checker.py').touch()
    (repo / 'scripts/verify').touch()
    config = default_config(repo)
    config['source_checks'][0]['timeout_ms'] = 750
    policy = pin_policy(repo, tmp_path / 'state', config)
    assert policy['mandatory_source_checks'][0]['timeout_ms'] == 750
    for timeout in (500, 1000):
        changed = deepcopy(config)
        changed['source_checks'][0]['timeout_ms'] = timeout
        with pytest.raises(ValueError):
            check_policy(changed, policy)
    removed = deepcopy(config)
    removed['source_checks'][0]['ensures'] = 'True'
    with pytest.raises(ValueError):
        pin_policy(repo, tmp_path / 'weak-state', removed)


def test_case_check_key_changes_when_configured_timeout_changes(checked_case_repo, tmp_path):
    repo, head, config = checked_case_repo
    coordinator, _ = execute_check(repo, head, config, tmp_path, ReplayWorker())
    key = coordinator._check_key(head, [], [], None)
    coordinator.config['source_checks'][0]['timeout_ms'] = 750
    assert coordinator._check_key(head, [], [], None) != key


def test_public_replay_program_matches_worker_execution_and_review_context(checked_case_repo, tmp_path, monkeypatch):
    import hashlib
    import sys

    from mentat.autonomy_runtime import LocalWorker, reproduction_program
    from mentat.source_verify import verify_source

    repo, head, config = checked_case_repo
    contract = config['source_checks'][0]
    negative = verify_source(repo, head, 'bad.py', 'bad_clamp', contract['inputs'],
                             contract['requires'], contract['ensures'])
    assert negative['status'] == 'counterexample'
    negative['id'] = 'case-evidence'
    worker = LocalWorker()
    commands = []

    def guest(repo, rev, argv, *, timeout_seconds):
        commands.append(argv)
        result = subprocess.run([sys.executable, *argv[1:]], text=True, capture_output=True,
                                timeout=10, check=False)
        return {'status': 'succeeded' if result.returncode == 0 else 'failed',
                'returncode': result.returncode, 'stdout': result.stdout,
                'tools': {'selected_python_version': 'Python ' + sys.version.split()[0]}}

    monkeypatch.setattr(worker, 'run', guest)
    replay = worker.reproduce(repo, head, negative)
    assert replay['status'] == 'passed'
    assert replay['witness'] == {'x': 0} and replay['postcondition'] is False
    assert len(commands) == 1
    program = reproduction_program()
    assert commands[0][4] == program
    coordinator, _ = execute_check(repo, head, config, tmp_path, ReplayWorker())
    text, _, _, _ = coordinator._source_context(head, {'payload': {'kind': 'generate_verification'}})
    context = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    trusted = context['trusted_source_verifier']
    assert trusted['replay_source'] == program
    assert trusted['replay_sha256'] == hashlib.sha256(program.encode()).hexdigest()
