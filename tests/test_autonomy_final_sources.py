import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

import pytest
from test_autonomy import Agent, GitHub, _review_capacity_repo, git, run
from test_autonomy import repo as repo_fixture


def test_current_source_batches_precede_aggregate_and_publication(tmp_path, monkeypatch):
    repo = repo_fixture.__wrapped__(tmp_path)
    requirements, paths = _review_capacity_repo(repo)
    for path in paths:
        (repo / path).write_text('# source\n' + 'x' * 55_000 + '\n')
    git(repo, 'commit', '-qam', 'large source inventory')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    calls = []

    class SourceReviewAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            payload = job['payload']
            kind = payload['kind']
            context = json.loads(payload['prompt'].split('SOURCE_CONTEXT_JSON:\n', 1)[1])
            calls.append((job['id'], kind, payload, context))
            if kind == 'generate_spec':
                fas_path = repo / '.mentat/fas.json'
                fas = json.loads(fas_path.read_text())
                for surface in fas['surfaces']:
                    if surface['path'] in paths and surface['path'] in context['files']:
                        surface.update(status='mapped', requirement_ids=[requirements[0]['id']])
                fas_path.write_text(json.dumps(fas))
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds) if kind not in {
                'generate_spec', 'generate_verification'} else {
                    'status': 'succeeded', 'session_id': job['id'], 'result': {
                        'decision': 'accept', 'summary': 'reviewable change', 'findings': [],
                        'verification_requests': []}}
            if kind == 'review_final_source':
                result['result']['summary'] = json.dumps({
                    'findings': 'Inspected the supplied source and its mapped requirements.',
                    'unresolved_dependencies': []})
            return result

    snapshot, _, github = run(repo, tmp_path, agent=SourceReviewAgent())
    assert github.published, snapshot
    source_calls = [call for call in calls if call[1] == 'review_final_source']
    aggregate_calls = [call for call in calls if call[1] == 'review_final']
    assert len(aggregate_calls) == 2
    assert len(source_calls) >= 4
    assert max(i for i, call in enumerate(calls) if call[1] == 'review_final_source') < min(
        i for i, call in enumerate(calls) if call[1] == 'review_final')
    groups = defaultdict(list)
    for job_id, _, payload, context in source_calls:
        groups[payload['source_batch']['id']].append((job_id, payload, context))
    assert len(groups) >= 2
    covered = set()
    for group in groups.values():
        assert len(group) == 2 and group[0][0] != group[1][0]
        for _, _, context in group:
            assert context['revision'] == github.published[0]
            assert len(json.dumps(context, ensure_ascii=True)) <= 500_000
            assert context['source_batch']['records']
            assert context['source_batch']['current_fas_digest']
            assert context['files']['openspec/specs/application/spec.md']
            for path in paths:
                if path in context['files']:
                    assert context['files'][path] == (repo / path).read_text()
                    covered.add(path)
    assert set(paths) <= covered
    for _, _, payload, context in aggregate_calls:
        assert context['revision'] == github.published[0] == payload['check_summary']['head']
        assert context['proposal_diff'] and context['proposal_base'] == snapshot['session']['base']
        assert context['trusted_source_verifier']['source']
        assert context['source_coverage']['batches']
        assert context['files']['.mentat/fas.json']
    source_ids = {job_id for job_id, *_ in source_calls}
    decisions = [row for row in snapshot['decisions'] if row.get('source_batch_id') in groups]
    assert len(decisions) == len(source_ids)
    for batch_id in groups:
        pair = [row for row in decisions if row['source_batch_id'] == batch_id]
        assert len(pair) == 2
        assert len({row['reviewer_id'] for row in pair}) == 2
        assert all(row['reviewer_id'] != row['author_id'] for row in pair)
    for decision in decisions:
        manifest_path = tmp_path / 'state' / decision['input_manifest']
        assert manifest_path.stat().st_mode & 0o777 == 0o600
        manifest_bytes = manifest_path.read_bytes()
        assert hashlib.sha256(manifest_bytes).hexdigest() == decision['input_manifest_sha256']
        manifest = json.loads(manifest_bytes)
        _, _, payload, context = next(call for call in source_calls if call[0] == decision['job_id'])
        assert manifest['job_id'] == decision['job_id']
        assert manifest['source_batch']['id'] == decision['source_batch_id']
        assert manifest['head'] == context['revision']
        assert manifest['omitted'] == context['omitted']
        prompt_path = manifest_path.parent / f"prompt-{manifest['prompt_sha256']}.txt"
        assert prompt_path.stat().st_mode & 0o777 == 0o600
        prompt_bytes = prompt_path.read_bytes()
        assert prompt_bytes == payload['prompt'].encode()
        assert hashlib.sha256(prompt_bytes).hexdigest() == manifest['prompt_sha256']
        supplied = {entry['path']: entry for entry in manifest['files']}
        assert set(supplied) == set(context['files'])
        for path, content in context['files'].items():
            assert supplied[path]['content_sha256'] == hashlib.sha256(content.encode()).hexdigest()
            tree = git(repo, 'ls-tree', context['revision'], '--', path).split()
            assert supplied[path]['mode'] == tree[0] and supplied[path]['blob'] == tree[2]

    # Revalidate the actual completed jobs, then tamper with one persisted fact
    # at a time. A forged accepted record cannot turn another job into consent.
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    store = Store(tmp_path / 'state/state.db')
    durable = store.snapshot(snapshot['session']['id'])
    reference = decisions[0]
    source_job = next(job for job in durable['jobs'] if job['id'] == reference['job_id'])
    proposal = source_job['payload']['proposal']
    evidence = next(item for item in durable['evidence'] if item['id'] == reference['check_evidence_id'])
    coordinator = Coordinator(repo, store, Agent(), None, github)
    coordinator.session_id = snapshot['session']['id']
    coordinator.config = json.loads((repo / '.mentat/workspace.json').read_text())
    first_manifest = json.loads((tmp_path / 'state' / reference['input_manifest']).read_bytes())
    # Exercise the real Codex adapter's argument construction with the exact
    # saved prompt, replacing only the external subprocess.
    from mentat import autonomy_runtime

    supplied_prompt = next(call[2]['prompt'] for call in source_calls if call[0] == reference['job_id'])
    runtime_argv = []
    original_run = autonomy_runtime._run
    original_execute = autonomy_runtime.CodexAgent._execute
    features = ('shell_tool', 'unified_exec', 'apps', 'browser_use', 'computer_use',
                'multi_agent', 'code_mode', 'code_mode_host')
    monkeypatch.setattr(autonomy_runtime, '_run', lambda argv, **kwargs:
                        '--ignore-user-config --disable' if '--help' in argv else
                        '\n'.join(f'{feature} stable true' for feature in features))

    def capture_execute(self, argv, **kwargs):
        runtime_argv.append(argv)
        answer = {'schema_version': 1, 'decision': 'accept', 'summary': 'Reviewed',
                  'requirements': [], 'claims': [], 'findings': [], 'verification_requests': [],
                  'defects': [], 'changes': []}
        Path(argv[argv.index('--output-last-message') + 1]).write_text(json.dumps(answer))
        return '{"type":"thread.started","thread_id":"test-thread"}\n'

    monkeypatch.setattr(autonomy_runtime.CodexAgent, '_execute', capture_execute)
    runtime_result = autonomy_runtime.CodexAgent().run(
        repo, {'payload': {'prompt': supplied_prompt, 'purpose': 'discriminator'}},
        tmp_path / 'runtime-check', timeout_seconds=5)
    monkeypatch.setattr(autonomy_runtime, '_run', original_run)
    monkeypatch.setattr(autonomy_runtime.CodexAgent, '_execute', original_execute)
    assert runtime_result['status'] == 'succeeded'
    assert runtime_argv[0][-1].encode() == (tmp_path / 'state' / reference['input_manifest']).parent.joinpath(
        f"prompt-{first_manifest['prompt_sha256']}.txt").read_bytes()
    coordinator.authority_digest = first_manifest['policy_digest']
    coordinator.tool_identity = first_manifest['tool_identity']

    class SnapshotStore:
        path = store.path

        def __init__(self, snapshot):
            self.value = snapshot

        def snapshot(self, session_id):
            assert session_id == coordinator.session_id
            return self.value

    coordinator.store = SnapshotStore(durable)
    assert coordinator._final_source_coverage(proposal, evidence)['batches']
    for fault in ('defer_outcome', 'revise_outcome', 'wrong_cohort', 'wrong_attempt',
                  'wrong_head', 'wrong_diff', 'wrong_check', 'wrong_kind', 'wrong_reviewer', 'defect_outcome',
                  'cross_batch', 'wrong_role', 'missing_manifest'):
        altered = deepcopy(durable)
        altered_decision = next(row for row in altered['decisions'] if row['id'] == reference['id'])
        altered_job = next(row for row in altered['jobs'] if row['id'] == reference['job_id'])
        if fault in {'defer_outcome', 'revise_outcome'}:
            altered_job['outcome']['result']['decision'] = fault.split('_')[0]
        elif fault == 'defect_outcome':
            altered_job['outcome']['result']['defects'] = [{'summary': 'unresolved defect'}]
        elif fault == 'wrong_cohort':
            altered_job['payload']['review_cohort'] = 'different'
        elif fault == 'wrong_attempt':
            altered_job['attempt'] += 1
        elif fault == 'wrong_head':
            altered_job['outcome']['head'] = '0' * 40
        elif fault == 'wrong_diff':
            altered_job['outcome']['diff_digest'] = '0' * 64
        elif fault == 'wrong_check':
            altered_job['payload']['check_key'] = '0' * 64
        elif fault == 'wrong_kind':
            altered_job['payload']['kind'] = 'review_spec'
        elif fault == 'cross_batch':
            altered_job['payload']['source_batch'] = next(
                call[2]['source_batch'] for call in source_calls
                if call[2]['source_batch']['id'] != reference['source_batch_id'])
        elif fault == 'wrong_role':
            altered_job['role'] = 'implementer'
        elif fault == 'missing_manifest':
            altered_decision.pop('input_manifest')
        else:
            altered_decision['reviewer_id'] = proposal['author_id']
        coordinator.store = SnapshotStore(altered)
        with pytest.raises(ValueError):
            coordinator._final_source_coverage(proposal, evidence)

    coordinator.store = SnapshotStore(durable)
    manifest_path = tmp_path / 'state' / reference['input_manifest']
    prompt_path = manifest_path.parent / f"prompt-{first_manifest['prompt_sha256']}.txt"
    original_prompt = prompt_path.read_bytes()
    try:
        prompt_path.write_bytes(original_prompt + b'forged')
        with pytest.raises(ValueError):
            coordinator._final_source_coverage(proposal, evidence)
        aggregate_proposal = aggregate_calls[0][2]['proposal']
        final_reviews = [row for row in durable['decisions']
                         if row.get('review_kind') == 'review_final']
        github_spy = GitHub()
        coordinator.github = github_spy
        with pytest.raises(ValueError, match='source prompt'):
            coordinator._publish({'payload': {'proposal': aggregate_proposal,
                                             'reviews': final_reviews}}, evidence)
        assert not github_spy.published and not github_spy.merged
    finally:
        prompt_path.write_bytes(original_prompt)
    try:
        prompt_path.unlink()
        prompt_path.symlink_to(tmp_path / 'untrusted-prompt')
        with pytest.raises((ValueError, OSError)):
            coordinator._final_source_coverage(proposal, evidence)
    finally:
        prompt_path.unlink()
        prompt_path.write_bytes(original_prompt)
        prompt_path.chmod(0o600)


def test_plan_tracks_old_and_new_mapping_and_requirement_citations():
    from mentat.autonomy_final_review import build_plan

    base = {'requirements': [{'id': 'req', 'citations': ['old_evidence.py:1']}],
            'claims': [], 'surfaces': [{'path': 'removed.py', 'status': 'mapped',
                                       'requirement_ids': ['req']}]}
    current = {'requirements': [{'id': 'req', 'citations': ['new_evidence.py#L1']}],
               'claims': [], 'surfaces': [{'path': 'renamed.py', 'status': 'mapped',
                                          'requirement_ids': ['req']},
                                         {'path': 'unknown.py', 'status': 'unexamined',
                                          'requirement_ids': []}]}
    entries = {path: {'base': {'blob': 'old', 'mode': '100644'},
                      'current': {'blob': 'new', 'mode': '100644'}}
               for path in ['removed.py', 'renamed.py', 'old_evidence.py', 'new_evidence.py']}
    entries['removed.py']['current'] = None
    entries['renamed.py']['base'] = None
    plan = build_plan(base, current, ['removed.py', 'renamed.py'], entries, {})
    obligations = {row['path']: row for row in plan['obligations']}
    assert set(obligations) == set(entries)
    assert obligations['removed.py']['current'] is None
    assert obligations['removed.py']['base_surface']['status'] == 'mapped'
    assert obligations['renamed.py']['current_surface']['status'] == 'mapped'
    assert plan['affected_requirements'] == ['req']
    assert [gap['path'] for gap in plan['gaps']] == ['unknown.py']


def test_plan_citation_prefers_actual_filename_over_line_anchor():
    from mentat.autonomy_final_review import build_plan

    base = {'requirements': [], 'claims': [], 'surfaces': []}
    current = {'requirements': [{'id': 'req', 'citations': ['literal.py#L1']}],
               'claims': [], 'surfaces': []}
    entries = {'literal.py#L1': {'base': None, 'current': {'blob': 'blob', 'mode': '100644'}}}
    plan = build_plan(base, current, [], entries, {})
    assert [row['path'] for row in plan['obligations']] == ['literal.py#L1']


@pytest.mark.parametrize('fault', ['oversized', 'dependency', 'invalid_summary', 'missing_findings'])
def test_final_source_findings_fail_closed(fault):
    from mentat.autonomy_final_review import validate_findings

    summary = {'findings': 'The implementation matches the cited scope.',
               'unresolved_dependencies': []}
    result = {'decision': 'accept', 'summary': json.dumps(summary),
              'findings': [{'summary': 'Scope and assumptions reviewed.'}],
              'requirements': [], 'claims': [], 'verification_requests': [], 'defects': [], 'changes': []}
    if fault == 'oversized':
        result['findings'][0]['summary'] = 'x' * 4097
    elif fault == 'dependency':
        summary['unresolved_dependencies'] = ['Inspect external caller before acceptance']
        result['summary'] = json.dumps(summary)
    elif fault == 'invalid_summary':
        result['summary'] = 'unstructured acceptance'
    else:
        del result['findings']
    with pytest.raises(ValueError):
        validate_findings(result)


def test_final_source_findings_remain_complete():
    from mentat.autonomy_final_review import validate_findings

    summary = {'findings': 'Inspected both caller and declared precondition.',
               'unresolved_dependencies': []}
    findings = [{'summary': 'Exact caller scope', 'detail': 'Concrete source evidence'}]
    result = {'decision': 'accept', 'summary': json.dumps(summary), 'findings': findings,
              'requirements': [], 'claims': [], 'verification_requests': [], 'defects': [], 'changes': []}
    assert validate_findings(result) == summary
    assert result['findings'] == findings


def test_large_current_source_with_v6_sized_fas_and_cas_fits_batch(tmp_path):
    import mentat.autonomy as autonomy
    from mentat.autonomy import Coordinator
    from mentat.autonomy_final_review import batch, build_plan
    from mentat.autonomy_state import Store

    repo = repo_fixture.__wrapped__(tmp_path)
    source = repo / 'large_source.py'
    source.write_bytes(Path(autonomy.__file__).read_bytes())
    assert 200_000 < source.stat().st_size <= 240_000
    spec = repo / 'openspec/specs/application/spec.md'
    spec.parent.mkdir(parents=True)
    spec.write_text('CAS record\n' + 's' * 37_000)
    active = {'id': 'active', 'statement': 'Relevant source requirement', 'citations': []}
    fas = {'schema_version': 1, 'requirements': [active, *[
        {'id': f'other-{i}', 'statement': 'unrelated ' + 'x' * 180, 'citations': []}
        for i in range(400)]], 'claims': [],
        'surfaces': [{'path': 'large_source.py', 'status': 'mapped', 'requirement_ids': ['active']}]}
    fas_path = repo / '.mentat/fas.json'
    fas_path.write_text(json.dumps(fas))
    assert 80_000 < fas_path.stat().st_size < 120_000
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'large proof source and complete specification')
    head = git(repo, 'rev-parse', 'HEAD')
    tree = git(repo, 'ls-tree', head, '--', 'large_source.py').split()
    entry = {'mode': tree[0], 'blob': tree[2], 'type': tree[1]}
    plan = build_plan(fas, fas, ['large_source.py'], {
        'large_source.py': {'base': entry, 'current': entry}}, {})
    scope = batch(plan, plan['obligations'])
    coordinator = Coordinator(repo, Store(tmp_path / 'large-state/state.db'), Agent(), None, None)
    text, _, _, _ = coordinator._source_context(head, {'payload': {
        'kind': 'review_final_source', 'proposal': {'parent': head},
        'targetfiles': ['large_source.py'], 'source_batch': scope}})
    data = json.loads(text.split('SOURCE_CONTEXT_JSON:\n', 1)[1])
    assert data['files']['large_source.py'] == source.read_text()
    assert data['files']['openspec/specs/application/spec.md'] == spec.read_text()
    assert active in data['source_batch']['records']['current']['requirements']
    assert len(json.dumps(data, ensure_ascii=True)) <= 500_000
    assert sum(len(json.dumps({path: content}, ensure_ascii=True))
               for path, content in data['files'].items()) <= 360_000


@pytest.mark.parametrize('fault', ['missing', 'oversized', 'unsafe', 'symlink'])
def test_required_source_batch_input_fails_closed(tmp_path, fault):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    repo = repo_fixture.__wrapped__(tmp_path)
    path = repo / 'candidate.py'
    if fault == 'oversized':
        path.write_text('x' * 240_001)
    elif fault == 'symlink':
        path.symlink_to('src.py')
    elif fault != 'missing':
        path.write_text('small source\n')
    if fault != 'missing':
        git(repo, 'add', '.')
        git(repo, 'commit', '-qm', 'candidate source')
    head = git(repo, 'rev-parse', 'HEAD')
    source_path = '../escape' if fault == 'unsafe' else 'candidate.py'
    scope = {'id': 'scope', 'plan_id': 'plan', 'records': {}, 'obligations': [{
        'path': source_path, 'mode': 'current-source', 'base': None, 'current': None}]}
    coordinator = Coordinator(repo, Store(tmp_path / 'unsafe-state/state.db'), Agent(), None, None)
    with pytest.raises((ValueError, OSError)):
        coordinator._source_context(head, {'payload': {
            'kind': 'review_final_source', 'proposal': {'parent': head},
            'targetfiles': [source_path], 'source_batch': scope}})


@pytest.fixture
def private_input(tmp_path):
    from mentat.autonomy_review_inputs import persist

    repo = repo_fixture.__wrapped__(tmp_path)
    head = git(repo, 'rev-parse', 'HEAD')
    root = tmp_path / 'private-state'
    root.mkdir(mode=0o700)
    scope = {'id': 'batch', 'obligations': [{'path': 'src.py', 'mode': 'current-source'}]}
    job = {'id': 'abc123', 'attempt': 1}
    context = '\nSOURCE_CONTEXT_JSON:\n' + json.dumps({
        'revision': head, 'files': {'src.py': (repo / 'src.py').read_text()},
        'omitted': [], 'source_batch': scope})
    prompt = 'Inspect the source.' + context
    bindings = {'head': head, 'source_batch': scope, 'job_id': job['id'], 'attempt': 1}
    def write():
        return persist(repo, root, job, prompt, context, bindings)
    return repo, root, job, scope, bindings, prompt, write


def test_private_inputs_are_idempotent_and_content_addressed(private_input):
    from mentat.autonomy_review_inputs import validate

    repo, root, job, scope, bindings, prompt, write = private_input
    first = write()
    assert write() == first
    manifest = root / first['input_manifest']
    assert manifest.stat().st_mode & 0o777 == 0o600
    decision = {key: first[key] for key in ('input_manifest', 'input_manifest_sha256')}
    job['outcome'] = {'prompt_sha256': first['prompt_sha256']}
    validate(repo, root, job, decision, bindings, prompt, scope)
    manifest.write_bytes(b'corrupted')
    with pytest.raises(ValueError):
        write()
    with pytest.raises(ValueError):
        validate(repo, root, job, decision, bindings, prompt, scope)


@pytest.mark.parametrize('fault', ['symlink_directory', 'writable_parent', 'symlink_file', 'wrong_file_mode'])
def test_private_inputs_reject_unsafe_paths_and_permissions(private_input, fault, tmp_path):
    _, root, _, _, _, _, write = private_input
    if fault == 'symlink_directory':
        (root / 'agents').symlink_to(tmp_path)
    elif fault == 'writable_parent':
        (root / 'agents').mkdir(mode=0o700)
        (root / 'agents').chmod(0o777)
    else:
        first = write()
        manifest = root / first['input_manifest']
        if fault == 'symlink_file':
            manifest.unlink()
            manifest.symlink_to(tmp_path / 'external')
        else:
            manifest.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        write()


def test_private_input_read_enforces_bound_after_file_growth(tmp_path, monkeypatch):
    import os

    from mentat import autonomy_review_inputs

    file = tmp_path / 'input'
    file.write_bytes(b'x')
    file.chmod(0o600)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    reads = iter([b'x' * 101, b''])
    monkeypatch.setattr(autonomy_review_inputs.os, 'read', lambda *_: next(reads))
    try:
        with pytest.raises(ValueError):
            autonomy_review_inputs._read(directory, 'input', 100)
    finally:
        os.close(directory)


@pytest.mark.parametrize('fault', ['expired', 'attempt', 'finished', 'missing'])
def test_review_input_requires_current_live_attempt(tmp_path, fault):
    from mentat.autonomy import Coordinator
    from mentat.autonomy_state import Store

    repo = repo_fixture.__wrapped__(tmp_path)
    store = Store(tmp_path / 'lease-state/state.db')
    coordinator = Coordinator(repo, store, Agent(), None, None, clock=lambda: 100)
    coordinator.session_id = 'session'
    job = {'id': 'abc123', 'attempt': 2}
    running = {'id': job['id'], 'status': 'running', 'attempt': 2, 'lease_until': 101}
    if fault == 'expired':
        running['lease_until'] = 100
    elif fault == 'attempt':
        running['attempt'] = 1
    elif fault == 'finished':
        running['status'] = 'succeeded'

    class SnapshotStore:
        path = store.path
        def snapshot(self, session_id):
            return {'jobs': [] if fault == 'missing' else [running]}

    coordinator.store = SnapshotStore()
    with pytest.raises(ValueError, match='lease'):
        coordinator._source_input_artifacts(job, 'prompt', 'context')


@pytest.mark.parametrize('field', ['head', 'job_id', 'attempt', 'source_batch', 'policy_digest', 'tool_identity'])
def test_private_manifest_rejects_changed_binding(private_input, field):
    from mentat.autonomy_review_inputs import validate

    repo, root, job, scope, bindings, prompt, write = private_input
    saved = write()
    job['outcome'] = {'prompt_sha256': saved['prompt_sha256']}
    decision = {key: saved[key] for key in ('input_manifest', 'input_manifest_sha256')}
    altered = {**bindings, field: 'different'}
    with pytest.raises(ValueError):
        validate(repo, root, job, decision, altered, prompt, scope)


@pytest.mark.parametrize('field', ['requirements', 'claims', 'verification_requests', 'defects', 'changes'])
@pytest.mark.parametrize('fault', ['missing', 'wrong_type', 'nonempty'])
def test_accepted_source_result_requires_empty_unsupported_fields(field, fault):
    from mentat.autonomy_final_review import validate_findings

    result = {'decision': 'accept', 'summary': json.dumps({
        'findings': 'Reviewed source scope.', 'unresolved_dependencies': []}), 'findings': [],
        'requirements': [], 'claims': [], 'verification_requests': [], 'defects': [], 'changes': []}
    if fault == 'missing':
        del result[field]
    elif fault == 'wrong_type':
        result[field] = None
    else:
        result[field] = [{'summary': 'Unprocessed concern'}]
    with pytest.raises(ValueError):
        validate_findings(result)


def test_accepted_source_defect_never_reaches_aggregate_or_publication(tmp_path):
    repo = repo_fixture.__wrapped__(tmp_path)
    calls = []

    class DefectAgent(Agent):
        def run(self, repo, job, output, *, timeout_seconds):
            kind = job['payload']['kind']
            calls.append(kind)
            result = super().run(repo, job, output, timeout_seconds=timeout_seconds)
            if kind == 'review_final_source':
                result['result']['defects'] = [{'summary': 'Unresolved caller defect'}]
            return result

    snapshot, _, github = run(repo, tmp_path, agent=DefectAgent())
    assert 'review_final_source' in calls, snapshot
    assert 'review_final' not in calls
    assert not github.published and not github.merged
    assert not any(row.get('review_kind') == 'review_final_source' and row.get('status') == 'passed'
                   for row in snapshot['decisions'])


@pytest.mark.parametrize('operation', ['persist', 'validate'])
def test_private_input_fifo_without_writer_fails_without_hanging(private_input, tmp_path, operation):
    import os
    import subprocess
    import sys

    repo, root, job, scope, bindings, prompt, write = private_input
    saved = write()
    manifest = root / saved['input_manifest']
    manifest.unlink()
    os.mkfifo(manifest, 0o600)
    payload = {'repo': str(repo), 'root': str(root), 'job': job, 'scope': scope,
               'bindings': bindings, 'prompt': prompt, 'saved': saved, 'operation': operation}
    payload_path = tmp_path / 'fifo-case.json'
    payload_path.write_text(json.dumps(payload))
    script = '''
import json, sys
from pathlib import Path
from mentat.autonomy_review_inputs import persist, validate
value = json.loads(Path(sys.argv[1]).read_text())
repo, root = Path(value['repo']), Path(value['root'])
job, saved = value['job'], value['saved']
try:
    if value['operation'] == 'persist':
        context = value['prompt'][value['prompt'].index('\\nSOURCE_CONTEXT_JSON:\\n'):]
        persist(repo, root, job, value['prompt'], context, value['bindings'])
    else:
        job['outcome'] = {'prompt_sha256': saved['prompt_sha256']}
        validate(repo, root, job, saved, value['bindings'], value['prompt'], value['scope'])
except (ValueError, OSError):
    sys.exit(0)
sys.exit(1)
'''
    result = subprocess.run([sys.executable, '-c', script, str(payload_path)],
                            capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 0, result.stderr
