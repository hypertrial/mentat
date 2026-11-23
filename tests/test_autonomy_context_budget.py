import json
import subprocess
import uuid
from copy import deepcopy

import pytest

from mentat.autonomy import (
    Coordinator,
    ReviewContextCapacity,
    current_tool_identity,
    default_config,
)
from mentat.autonomy_state import Store


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def commit(repo, message):
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', message)
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def budget_history(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'user.email', 'test@example.com')
    (repo / '.mentat').mkdir()
    config = default_config(repo)
    check = {'path': 'subject.py', 'function': 'clamp', 'inputs': {'x': 'int'},
             'requires': 'True', 'ensures': '0 <= result <= 10',
             'counterexample_cases': [{'path': 'bad.py', 'function': 'bad'}]}
    config['source_checks'] = [check]
    (repo / '.mentat/workspace.json').write_text(json.dumps(config))
    (repo / 'subject.py').write_text('def clamp(x): return max(0, min(10, x))\n')
    (repo / 'bad.py').write_text('def bad(x): return -1\n')
    (repo / 'changed.py').write_text('value = 1\n')
    spec = repo / 'openspec/specs/app/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('Original specification\n')
    record = {'id': str(uuid.uuid4()), 'statement': 'Observed', 'citations': ['subject.py']}
    fas = {'schema_version': 1, 'requirements': [record], 'claims': [], 'surfaces': []}
    (repo / '.mentat/fas.json').write_text(json.dumps(fas))
    optional = []
    for i in range(7):
        path = f'optional{i}.py'
        (repo / path).write_text('# optional\n' + 'x' * 49_000)
        optional.append(path)
    folder = repo / ('omitted_' + 'a' * 140)
    folder.mkdir()
    omitted = []
    for i in range(260):
        path = folder / f'file{i:03}.py'
        path.write_text('# oversized optional\n' + 'x' * 65_000)
        omitted.append(str(path.relative_to(repo)))
    base = commit(repo, 'base source and inventory')
    spec.write_text('Original specification\nCumulative change\n')
    (repo / 'changed.py').write_text('value = 2\n')
    head = commit(repo, 'candidate specification')
    return repo, config, check, record, base, head, optional, omitted


def final_context(history, tmp_path, monkeypatch, *, mandatory_growth=80_000):
    repo, config, check, _, base, head, _, _ = history
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), None, None, None)
    coordinator.config = config
    coordinator.tool_identity = current_tool_identity()
    composition = {'checkpoint': head, 'cohorts': [{'decision_ids': ['review-a', 'review-b']}],
                   'baseline': 'unreviewed unless covered', 'metadata': 'm' * mandatory_growth}
    monkeypatch.setattr(coordinator, '_review_composition', lambda revision: deepcopy(composition))
    summary = {'status': 'passed', 'head': head, 'source_cases': [{'status': 'passed',
               'case': {'path': 'bad.py', 'function': 'bad'}, 'evidence': {'id': 'case-1'}}]}
    proposal = {'parent': base, 'head': head, 'observed_source_revision': base}
    payload = {'kind': 'review_final', 'proposal': proposal, 'check_summary': summary}
    text, omitted, included, deferred = coordinator._source_context(head, {'payload': payload})
    return json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1]), omitted, included, deferred, summary, composition, check


def test_final_context_evicts_optional_source_for_complete_omission_inventory(budget_history, tmp_path, monkeypatch):
    repo, _, _, record, base, head, optional, large_omissions = budget_history
    data, omitted, included, _, summary, composition, check = final_context(budget_history, tmp_path, monkeypatch)
    assert len(json.dumps(data, ensure_ascii=True)) <= 500_000
    assert data['revision'] == data['proposal_head'] == head
    assert data['proposal_base'] == base
    assert data['proposal_diff'] == git(repo, 'diff', '--no-ext-diff', '--no-textconv', base, head) + '\n'
    for path in ['.mentat/fas.json', '.mentat/workspace.json', 'openspec/specs/app/spec.md',
                 'subject.py', 'bad.py', 'changed.py']:
        assert data['files'][path] == (repo / path).read_text()
    assert data['mandatory_source_contracts'] == [check]
    assert data['reviewed_specification'] == composition
    assert data['trusted_source_verifier']['source']
    assert data['trusted_source_verifier']['replay_source']
    assert data['prior_revision_index'][record['id']]['version'] == 1
    assert record['id'] in data['patch_record_digest_index']['requirements']
    assert summary['source_cases'][0]['evidence']['id'] == 'case-1'
    assert set(large_omissions) <= omitted
    assert omitted == {row['path'] for row in data['omitted']}
    assert all(row['reason'] for row in data['omitted'])
    assert any(path in omitted for path in optional)
    assert any(path in included for path in optional)
    assert 'changed.py' not in omitted


def test_final_context_mandatory_overflow_raises_capacity(budget_history, tmp_path, monkeypatch):
    with pytest.raises(ReviewContextCapacity):
        final_context(budget_history, tmp_path, monkeypatch, mandatory_growth=425_000)


def test_both_final_review_jobs_receive_proof_and_complete_final_context(tmp_path):
    from test_autonomy_source_cases import ReplayWorker, checked_case_repo, run_case_workflow

    repo, _, config = checked_case_repo.__wrapped__(tmp_path)
    config['auto_merge'] = True
    snapshot, agent, github = run_case_workflow(repo, config, tmp_path, ReplayWorker())
    final = [(payload, data) for kind, payload, data, _ in agent.calls if kind == 'review_final']
    assert len(final) == 2, snapshot
    assert github.published
    for payload, data in final:
        assert len(json.dumps(data, ensure_ascii=True)) <= 500_000
        assert data['revision'] == data['proposal_head'] == payload['check_summary']['head']
        assert data['proposal_base'] == snapshot['session']['base']
        assert data['proposal_diff']
        assert data['reviewed_specification']['cohorts']
        assert data['files']['subject.py'] == (repo / 'subject.py').read_text()
        assert data['files']['bad.py'] == (repo / 'bad.py').read_text()
        assert data['files']['.mentat/fas.json']
        assert data['files']['openspec/specs/app/spec.md']
        assert data['trusted_source_verifier']['source']
        assert data['mandatory_source_contracts'] == config['source_checks']
        assert data['prior_revision_index'] and data['patch_record_digest_index']
        case = payload['check_summary']['source_cases'][0]
        assert case['status'] == 'passed'
        assert case['evidence']['source_identity']['revision'] == data['revision']
        assert case['reproduction']['evidence_id'] == case['evidence']['id']
    final_jobs = [job for job in snapshot['jobs'] if job['payload']['kind'] == 'review_final']
    assert len(final_jobs) == 2 and len({job['id'] for job in final_jobs}) == 2


def test_targeted_generation_keeps_progress_and_records_evicted_frontier(budget_history, tmp_path):
    repo, _, _, _, _, _, optional, oversized = budget_history
    fas_path = repo / '.mentat/fas.json'
    fas = json.loads(fas_path.read_text())
    fas['requirements'] = [{'id': str(uuid.uuid4()), 'statement': 'r' * 150}
                           for _ in range(760)]
    fas_path.write_text(json.dumps(fas))
    for path in oversized:
        (repo / path).write_text('x' * 241_000)
    head = commit(repo, 'large targeted source inventory')
    targets = optional[:3] + oversized
    coordinator = Coordinator(repo, Store(tmp_path / 'target-state/state.db'), None, None, None)
    text, omitted, included, deferred = coordinator._source_context(head, {'payload': {
        'kind': 'generate_spec', 'targetfiles': targets}})
    data = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert len(json.dumps(data, ensure_ascii=True)) <= 500_000
    assert len(set(targets) & included) == 1
    assert set(targets) - included <= omitted
    assert omitted == {item['path'] for item in data['omitted']}
    assert set(optional[:3]) & omitted <= set(deferred)
    assert any(path in omitted for path in optional[:3])
