import json
import subprocess
import uuid

import pytest

from mentat.autonomy import Coordinator, default_config
from mentat.autonomy_state import Store


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'user.email', 'test@example.com')
    (repo / '.mentat').mkdir()
    (repo / '.mentat/workspace.json').write_text(json.dumps(default_config(repo)))
    (repo / 'src.py').write_text('def value(): return 1\n')
    fas = {'schema_version': 1, 'requirements': [], 'claims': [], 'surfaces': []}
    (repo / '.mentat/fas.json').write_text(json.dumps(fas))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'observed source')
    old = git(repo, 'rev-parse', 'HEAD')
    blob = git(repo, 'rev-parse', f'{old}:src.py')
    yield repo, fas, old, blob


def commit(repo, message):
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', message)
    return git(repo, 'rev-parse', 'HEAD')


def write_fas(repo, fas):
    (repo / '.mentat/fas.json').write_text(json.dumps(fas))


def context(repo, tmp_path, head, payload):
    coordinator = Coordinator(repo, Store(tmp_path / 'state/state.db'), None, None, None)
    text, _, _, _ = coordinator._source_context(head, {'payload': payload})
    return json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])


def make_draft(history, citation):
    repo, fas, old, _ = history
    fas['requirements'].append({'id': str(uuid.uuid4()), 'statement': 'Observed',
                                'citations': [citation]})
    write_fas(repo, fas)
    intermediate = commit(repo, 'first unreviewed draft')
    spec = repo / 'openspec/specs/app/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('retry clarification\n')
    head = commit(repo, 'correction')
    return old, intermediate, head


def review_payload(base, observed):
    return {'kind': 'review_spec', 'targetfiles': [], 'proposal': {
        'parent': base, 'observed_source_revision': observed}}


@pytest.mark.parametrize('citation_format', ['source', 'revision'])
def test_retained_citation_exposes_candidate_bound_git_evidence(history, tmp_path, citation_format):
    repo, _, old, blob = history
    citation = {'path': 'src.py#L1'}
    citation.update({'source': f'task input revision {old}'} if citation_format == 'source'
                    else {'revision': old})
    base, observed, head = make_draft(history, citation)
    payloads = [review_payload(base, observed), review_payload(base, observed),
                {'kind': 'generate_spec', 'targetfiles': [], 'review_base': base}]
    for payload in payloads:
        result = context(repo, tmp_path, head, payload)
        assert result['files']['src.py'] == 'def value(): return 1\n'
        if payload['kind'] == 'review_spec':
            assert result['observed_source_revision'] == observed
            assert 'src.py' not in result['source_changes_since_observation']
        evidence = result['cited_source_continuity']
        item = next(item for item in evidence if item['path'] == 'src.py')
        assert item['cited_revision'] == old
        assert item['candidate_revision'] == head
        assert item['ancestor'] is True
        assert item['status'] == 'unchanged'
        assert item['cited_blob'] == item['candidate_blob'] == blob
        assert item['cited_mode'] == item['candidate_mode'] == '100644'


@pytest.mark.parametrize('change', ['content', 'mode', 'symlink', 'deleted'])
def test_source_drift_never_reports_unchanged(history, tmp_path, change):
    repo, _, old, _ = history
    base, observed, _ = make_draft(history, {'path': 'src.py', 'revision': old})
    source = repo / 'src.py'
    if change == 'content':
        source.write_text('def value(): return 2\n')
    elif change == 'mode':
        source.chmod(0o755)
    elif change == 'symlink':
        source.unlink()
        source.symlink_to('.mentat/workspace.json')
    else:
        source.unlink()
    head = commit(repo, change)
    try:
        result = context(repo, tmp_path, head, review_payload(base, observed))
    except (ValueError, OSError):
        return
    evidence = result['cited_source_continuity']
    assert evidence and all(item['status'] != 'unchanged' for item in evidence)


@pytest.mark.parametrize('invalid', ['unknown', 'malformed', 'conflict', 'nonancestor', 'unsafe'])
def test_invalid_provenance_never_reports_unchanged(history, tmp_path, invalid):
    repo, _, old, _ = history
    citation = {'path': 'src.py', 'revision': old}
    if invalid == 'unknown':
        citation['revision'] = 'f' * 40
    elif invalid == 'malformed':
        citation['revision'] = 'HEAD'
    elif invalid == 'conflict':
        citation['source'] = f'task input revision {"f" * 40}'
    elif invalid == 'unsafe':
        citation['path'] = '../outside.py'
    else:
        (repo / 'other.py').write_text('other branch\n')
        citation['revision'] = commit(repo, 'unrelated branch')
        git(repo, 'checkout', '-q', '--detach', old)
    base, observed, head = make_draft(history, citation)
    try:
        result = context(repo, tmp_path, head, review_payload(base, observed))
    except (ValueError, OSError):
        return
    assert result['cited_source_continuity']
    assert all(item['status'] != 'unchanged' for item in result['cited_source_continuity'])


def test_legacy_and_external_citations_do_not_create_git_evidence(history, tmp_path):
    repo, fas, old, _ = history
    fas['requirements'].append({'id': str(uuid.uuid4()), 'citations': [
        'src.py:1', {'path': 'src.py', 'source': 'observed baseline'},
        {'url': 'https://example.com/spec', 'revision': old}]})
    write_fas(repo, fas)
    head = commit(repo, 'legacy citations')
    result = context(repo, tmp_path, head, review_payload(old, old))
    assert result['files']['src.py'] == 'def value(): return 1\n'
    assert 'https://example.com/spec' not in result['files']
    assert not any(item['status'] == 'unchanged' for item in result.get('cited_source_continuity', []))


def test_continuity_metadata_count_is_bounded(history, tmp_path):
    repo, fas, old, _ = history
    for i in range(65):
        (repo / f'source{i}.py').write_text('source\n')
    base = commit(repo, 'source inventory')
    fas['requirements'].append({'id': str(uuid.uuid4()), 'citations': [
        {'path': f'source{i}.py', 'revision': base} for i in range(65)]})
    write_fas(repo, fas)
    head = commit(repo, 'too many references')
    with pytest.raises(ValueError, match='capacity|limit|exceed'):
        context(repo, tmp_path, head, review_payload(base, base))


def test_actual_reviewers_receive_retained_citation_on_retry(tmp_path):
    from test_autonomy import Agent, run
    from test_autonomy import git as test_git
    from test_autonomy import repo as repo_fixture

    repo = repo_fixture.__wrapped__(tmp_path)
    captured = []
    corrections = []

    class RetryAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            if payload['kind'] == 'generate_spec' and payload.get('round') == 1:
                corrections.append(json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1]))
                spec = repo / 'openspec/specs/application/spec.md'
                spec.write_text(spec.read_text() + '\nClarification\n')
                return {'status': 'succeeded', 'session_id': job['id'], 'result': {
                    'decision': 'accept', 'summary': 'revised', 'findings': [],
                    'verification_requests': []}}
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if payload['kind'] == 'generate_spec':
                source_revision = test_git(repo, 'rev-parse', 'HEAD')
                fas_path = repo / '.mentat/fas.json'
                fas = json.loads(fas_path.read_text())
                fas['requirements'][0]['citations'] = [
                    {'path': 'src.py', 'source': f'task input revision {source_revision}'}]
                fas_path.write_text(json.dumps(fas))
            elif payload['kind'] == 'review_spec':
                captured.append((job['id'], payload, json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])))
                if payload['proposal'].get('round') == 0:
                    result['result']['decision'] = 'revise'
                    result['result']['findings'] = [{'summary': 'Clarify'}]
            return result

    snapshot, _, _ = run(repo, tmp_path, agent=RetryAgent())
    retry = [(job_id, data) for job_id, payload, data in captured if payload['proposal'].get('round') == 1]
    assert len(retry) == 2, snapshot
    assert len(corrections) == 1
    for data in [corrections[0], *(data for _, data in retry)]:
        item = next(item for item in data['cited_source_continuity'] if item['path'] == 'src.py')
        assert item['status'] == 'unchanged'
        assert item['cited_revision'] == snapshot['session']['base']
        assert item['candidate_revision'] == data['revision']
        assert data['files']['src.py'] == 'def value(): return 1\n'
    assert len({job_id for job_id, _ in retry}) == 2


def test_refresh_includes_citations_from_unchanged_mapped_records(history, tmp_path):
    repo, fas, _, _ = history
    (repo / 'requirement_source.py').write_text('requirement source\n')
    (repo / 'claim_source.py').write_text('claim source\n')
    cited = commit(repo, 'cited sources')
    requirement_id = str(uuid.uuid4())
    fas['requirements'].append({
        'id': requirement_id,
        'citations': [{'path': 'requirement_source.py', 'revision': cited}],
    })
    fas['claims'].append({
        'id': str(uuid.uuid4()),
        'requirement_ids': [requirement_id],
        'revision': {'evidence': [{'path': 'claim_source.py',
                                  'source': f'task input revision {cited}'}]},
    })
    fas['surfaces'].append({'path': 'src.py', 'status': 'mapped',
                            'requirement_ids': [requirement_id]})
    write_fas(repo, fas)
    base = commit(repo, 'existing mapping')
    (repo / 'src.py').write_text('def value(): return 2\n')
    head = commit(repo, 'refresh mapped source')
    payload = review_payload(base, base)
    payload['correspondence_refresh'] = {'checkpoint': base, 'source_paths': ['src.py']}
    result = context(repo, tmp_path, head, payload)
    descriptors = {item['path']: item for item in result['cited_source_continuity']}
    for path in ['requirement_source.py', 'claim_source.py']:
        item = descriptors[path]
        assert item['cited_revision'] == cited
        assert item['candidate_revision'] == head
        assert item['status'] == 'unchanged'
        assert item['ancestor'] is True
        assert result['files'][path] == (repo / path).read_text()
