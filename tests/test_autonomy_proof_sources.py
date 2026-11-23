import json
import subprocess
import uuid
from copy import deepcopy

import pytest

from mentat.autonomy import Coordinator, current_tool_identity, default_config
from mentat.autonomy_state import Store


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def commit(repo, message):
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', message)
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def proof_repo(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'user.email', 'test@example.com')
    (repo / '.mentat').mkdir()
    config = default_config(repo)
    requirement, claim, other = (str(uuid.uuid4()) for _ in range(3))
    contract = {'path': 'subject.py', 'function': 'clamp', 'inputs': {'x': 'int'},
                'requires': 'True', 'ensures': '0 <= result <= 10'}
    check = {**contract, 'requirement_id': requirement, 'claim_id': claim,
             'counterexample_cases': [{'path': 'bad.py', 'function': 'bad'}]}
    config['source_checks'] = [check]
    (repo / '.mentat/workspace.json').write_text(json.dumps(config))
    contents = {'subject.py': 'def clamp(x): return max(0, min(10, x))\n',
                'bad.py': 'def bad(x): return -1\n',
                'z_contract.json': json.dumps(contract) + ' ' * 60_000,
                'z_requirement.txt': 'requirement observation\n',
                'z_claim.txt': 'claim observation\n',
                'zz_unrelated.py': 'x' * 60_000}
    for path, content in contents.items():
        (repo / path).write_text(content)
    for i in range(6):
        (repo / f'a_optional{i}.py').write_text('x' * 60_000)
    spec = repo / 'openspec/specs/app/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('Specification\n')
    fas = {'schema_version': 1,
           'requirements': [{'id': requirement, 'citations': ['z_requirement.txt:1']},
                            {'id': other, 'citations': []}],
           'claims': [{'id': claim, 'requirement_ids': [requirement],
                       'formal_contract': contract,
                       'citations': [{'path': 'z_claim.txt#L1'}, 'https://example.org/spec']}],
           'surfaces': [{'path': 'z_contract.json', 'status': 'mapped', 'requirement_ids': [requirement]},
                        {'path': 'zz_unrelated.py', 'status': 'mapped', 'requirement_ids': [other]}]}
    (repo / '.mentat/fas.json').write_text(json.dumps(fas))
    base = commit(repo, 'existing specification and sources')
    spec.write_text('Specification\nCandidate review\n')
    head = commit(repo, 'candidate')
    return repo, config, fas, contract, requirement, claim, base, head


def context(proof_repo, tmp_path, monkeypatch, kind, *, config=None, payload=None):
    repo, default, _, _, _, _, base, head = proof_repo
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), None, None, None)
    coordinator.config = config or default
    coordinator.tool_identity = current_tool_identity()
    monkeypatch.setattr(coordinator, '_review_composition', lambda revision: {
        'checkpoint': revision, 'cohorts': [{'decision_ids': ['one', 'two']}]})
    job_payload = {'kind': kind, **(payload or {})}
    if kind in {'review_verification', 'review_final'}:
        job_payload['proposal'] = {**job_payload.get('proposal', {}), 'parent': base, 'head': head}
        job_payload['check_summary'] = {'status': 'passed', 'head': head}
    text, omitted, included, _ = coordinator._source_context(head, {'payload': job_payload})
    return json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1]), omitted, included


@pytest.mark.parametrize('kind', ['generate_verification', 'review_verification', 'review_final'])
@pytest.mark.parametrize('identity', ['both', 'requirement', 'claim', 'contract'])
def test_active_requirement_sources_reserved_before_optional(proof_repo, tmp_path, monkeypatch, kind, identity):
    repo, config, _, _, _, _, base, head = proof_repo
    config = deepcopy(config)
    if identity in {'requirement', 'contract'}:
        config['source_checks'][0].pop('claim_id')
    if identity in {'claim', 'contract'}:
        config['source_checks'][0].pop('requirement_id')
    data, omitted, included = context(proof_repo, tmp_path, monkeypatch, kind, config=config)
    for path in ['subject.py', 'bad.py', 'z_contract.json', 'z_requirement.txt', 'z_claim.txt']:
        assert data['files'][path] == (repo / path).read_text()
        assert path in included and path not in omitted
    assert 'zz_unrelated.py' in omitted
    assert 'https://example.org/spec' not in data['files']
    assert data['mandatory_source_contracts'] == config['source_checks']
    assert data['trusted_source_verifier']['source']
    assert data['trusted_source_verifier']['replay_source']
    assert data['files']['.mentat/fas.json'] == (repo / '.mentat/fas.json').read_text()
    assert len(json.dumps(data, ensure_ascii=True)) <= 500_000
    if kind != 'generate_verification':
        assert data['proposal_base'] == base and data['proposal_head'] == head
        assert 'Candidate review' in data['proposal_diff']
        assert data['reviewed_specification']['checkpoint'] == head


@pytest.mark.parametrize('fault', ['unknown_requirement', 'unknown_claim', 'conflicting_ids', 'contract_mismatch'])
def test_explicit_identity_mismatch_fails_closed(proof_repo, tmp_path, monkeypatch, fault):
    _, config, fas, _, _, _, _, _ = proof_repo
    config = deepcopy(config)
    check = config['source_checks'][0]
    if fault == 'unknown_requirement':
        check['requirement_id'] = str(uuid.uuid4())
    elif fault == 'unknown_claim':
        check['claim_id'] = str(uuid.uuid4())
    elif fault == 'conflicting_ids':
        check['requirement_id'] = fas['requirements'][1]['id']
    else:
        check['ensures'] = 'False'
    with pytest.raises(ValueError):
        context(proof_repo, tmp_path, monkeypatch, 'review_final', config=config)


def test_idless_checks_include_all_matching_requirements(proof_repo, tmp_path, monkeypatch):
    repo, config, fas, contract, _, _, _, _ = proof_repo
    other = fas['requirements'][1]['id']
    fas['claims'].append({'id': str(uuid.uuid4()), 'requirement_ids': [other],
                          'formal_contract': contract, 'citations': []})
    (repo / '.mentat/fas.json').write_text(json.dumps(fas))
    head = commit(repo, 'second matching claim')
    updated = (*proof_repo[:-1], head)
    config = deepcopy(config)
    config['source_checks'][0].pop('claim_id')
    config['source_checks'][0].pop('requirement_id')
    data, _, _ = context(updated, tmp_path, monkeypatch, 'review_final', config=config)
    assert data['files']['z_contract.json'] == (repo / 'z_contract.json').read_text()
    assert data['files']['zz_unrelated.py'] == (repo / 'zz_unrelated.py').read_text()


@pytest.mark.parametrize('fault', ['missing', 'oversized', 'non_utf8', 'unsafe_citation'])
def test_required_proof_context_source_fails_closed(proof_repo, tmp_path, monkeypatch, fault):
    repo, _, fas, _, _, _, _, _ = proof_repo
    path = repo / 'z_contract.json'
    if fault == 'missing':
        path.unlink()
    elif fault == 'oversized':
        path.write_text('x' * 240_001)
    elif fault == 'non_utf8':
        path.write_bytes(b'\xff')
    else:
        fas['requirements'][0]['citations'] = ['../outside.txt']
        (repo / '.mentat/fas.json').write_text(json.dumps(fas))
    head = commit(repo, 'invalid active source')
    updated = (*proof_repo[:-1], head)
    with pytest.raises(ValueError):
        context(updated, tmp_path, monkeypatch, 'review_final')


@pytest.mark.parametrize('kind', ['generate_spec', 'generate_verification'])
def test_nonproof_context_does_not_promote_mappings(proof_repo, tmp_path, monkeypatch, kind):
    config = deepcopy(proof_repo[1])
    config['source_checks'][0].pop('counterexample_cases')
    data, omitted, _ = context(proof_repo, tmp_path, monkeypatch, kind, config=config)
    assert 'z_contract.json' in omitted
    assert 'z_contract.json' not in data['files']
    assert 'trusted_source_verifier' not in data


def test_unmapped_surface_is_not_promoted(proof_repo, tmp_path, monkeypatch):
    repo, _, fas, _, _, _, _, _ = proof_repo
    fas['surfaces'][0]['status'] = 'unexamined'
    (repo / '.mentat/fas.json').write_text(json.dumps(fas))
    head = commit(repo, 'unexamined surface')
    data, omitted, _ = context((*proof_repo[:-1], head), tmp_path, monkeypatch, 'review_final')
    assert 'z_contract.json' in omitted
    assert 'z_contract.json' not in data['files']
    assert json.loads(data['files']['.mentat/fas.json'])['surfaces'][0]['status'] == 'unexamined'


def test_no_fas_keeps_standalone_proof_source(proof_repo, tmp_path, monkeypatch):
    repo = proof_repo[0]
    (repo / '.mentat/fas.json').unlink()
    head = commit(repo, 'standalone source')
    data, _, _ = context((*proof_repo[:-1], head), tmp_path, monkeypatch, 'review_final')
    assert data['files']['subject.py'] == (repo / 'subject.py').read_text()
    assert data['files']['bad.py'] == (repo / 'bad.py').read_text()


def test_actual_proof_review_pairs_receive_mapped_contract(tmp_path):
    from test_autonomy_source_cases import (
        RecordingAgent,
        ReplayWorker,
        checked_case_repo,
        run_case_workflow,
    )

    repo, _, config = checked_case_repo.__wrapped__(tmp_path)
    requirement_id, claim_id = str(uuid.uuid4()), str(uuid.uuid4())
    config['auto_merge'] = True
    config['source_checks'][0].update(requirement_id=requirement_id, claim_id=claim_id)
    contract = {key: config['source_checks'][0][key]
                for key in ('path', 'function', 'inputs', 'requires', 'ensures')}
    (repo / 'contract.json').write_text(json.dumps(contract))

    class MappedAgent(RecordingAgent):
        def run(self, repo, job, output, *, timeout_seconds):
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if job['payload']['kind'] == 'generate_spec':
                fas_path = repo / '.mentat/fas.json'
                fas = json.loads(fas_path.read_text())
                old_id = fas['requirements'][0]['id']
                fas['requirements'][0]['id'] = requirement_id
                spec = repo / 'openspec/specs/app/spec.md'
                spec.write_text(spec.read_text().replace(old_id, requirement_id))
                fas['claims'].append({'id': claim_id, 'requirement_ids': [requirement_id],
                                      'statement': 'Clamp meets bounds', 'assumptions': [],
                                      'status': 'unattempted', 'formal_contract': contract})
                for row in fas['surfaces']:
                    if row['path'] == 'contract.json':
                        row.update(status='mapped', requirement_ids=[requirement_id])
                fas_path.write_text(json.dumps(fas))
            return result

    snapshot, agent, github = run_case_workflow(repo, config, tmp_path, ReplayWorker(), MappedAgent)
    assert github.published, snapshot
    for kind in ('review_verification', 'review_final'):
        pair = [(payload, data) for stage, payload, data, _ in agent.calls if stage == kind]
        assert len(pair) == 2
        for payload, data in pair:
            assert json.loads(data['files']['contract.json']) == contract
            assert data['files']['subject.py'] == (repo / 'subject.py').read_text()
            assert data['files']['bad.py'] == (repo / 'bad.py').read_text()
            assert data['revision'] == payload['check_summary']['head']
            assert data['reviewed_specification']['cohorts']
            case = payload['check_summary']['source_cases'][0]
            assert case['status'] == 'passed'
            assert case['reproduction']['evidence_id'] == case['evidence']['id']


def test_proposal_check_selects_linked_sources(proof_repo, tmp_path, monkeypatch):
    repo, config, _, _, _, _, _, _ = proof_repo
    check = deepcopy(config['source_checks'][0])
    config = deepcopy(config)
    config['source_checks'] = []
    data, _, _ = context(proof_repo, tmp_path, monkeypatch, 'review_final', config=config,
                         payload={'proposal': {'source_checks': [check]}})
    assert data['files']['z_contract.json'] == (repo / 'z_contract.json').read_text()
    assert data['files']['z_claim.txt'] == (repo / 'z_claim.txt').read_text()
    assert data['mandatory_source_contracts'] == [check]


def test_idless_unmatched_check_does_not_promote_mapping(proof_repo, tmp_path, monkeypatch):
    config = deepcopy(proof_repo[1])
    config['source_checks'][0].pop('claim_id')
    config['source_checks'][0].pop('requirement_id')
    config['source_checks'][0]['ensures'] = 'False'
    data, omitted, _ = context(proof_repo, tmp_path, monkeypatch, 'review_final', config=config)
    assert 'z_contract.json' in omitted and 'z_contract.json' not in data['files']
    assert 'subject.py' in data['files'] and 'bad.py' in data['files']


def test_combined_required_sources_over_budget_fail_closed(proof_repo, tmp_path, monkeypatch):
    repo = proof_repo[0]
    (repo / 'z_contract.json').write_text('x' * 190_000)
    (repo / 'z_claim.txt').write_text('x' * 190_000)
    head = commit(repo, 'indivisible required sources')
    with pytest.raises(ValueError, match='capacity'):
        context((*proof_repo[:-2], head, head), tmp_path, monkeypatch, 'review_final')
