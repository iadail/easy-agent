from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_common.models import RunContext
from agent_integrations.github_automation import (
    GitHubAutomationError,
    _slugify,
    git_commit_local,
    github_issue_list,
    github_issue_prepare_fix,
    github_release_publish,
)


@pytest.fixture
def run_context(tmp_path: Path) -> RunContext:
    return RunContext(run_id='run-1', workdir=tmp_path, node_id=None)


class CommandStub:
    def __init__(self, responses: dict[tuple[str, ...], subprocess.CompletedProcess[str]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: list[str], *, cwd: Path, timeout_seconds: float = 60.0, check: bool = True) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_seconds, check
        key = tuple(command)
        self.calls.append(key)
        if key not in self.responses:
            raise AssertionError(f'unexpected command: {command}')
        return self.responses[key]


def _ok(command: list[str], stdout: str = '', stderr: str = '') -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=stderr)


def test_slugify_normalizes_issue_titles() -> None:
    assert _slugify('Fix BFCL duplicate calls now!') == 'fix-bfcl-duplicate-calls-now'



def test_github_issue_list_requires_gh_install(monkeypatch: pytest.MonkeyPatch, run_context: RunContext) -> None:
    monkeypatch.setattr('agent_integrations.github_automation.shutil.which', lambda name: None)

    with pytest.raises(GitHubAutomationError, match='gh'):
        github_issue_list({}, run_context)



def test_github_issue_list_returns_structured_issues(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_context: RunContext) -> None:
    repo_root = tmp_path.resolve()
    run_context.workdir = repo_root
    monkeypatch.setattr('agent_integrations.github_automation.shutil.which', lambda name: 'gh.exe')
    stub = CommandStub(
        {
            ('git', 'rev-parse', '--show-toplevel'): _ok(['git'], stdout=str(repo_root)),
            ('gh', 'auth', 'status'): _ok(['gh']),
            (
                'gh',
                'issue',
                'list',
                '--limit',
                '5',
                '--state',
                'open',
                '--json',
                'number,title,state,labels,assignees,url,updatedAt,author',
                '--label',
                'bug',
                '--search',
                'federation',
            ): _ok(
                ['gh'],
                stdout=json.dumps(
                    [
                        {
                            'number': 12,
                            'title': 'Federation retry bug',
                            'state': 'OPEN',
                            'labels': [{'name': 'bug'}],
                            'assignees': [{'login': 'alice'}],
                            'url': 'https://example.com/issues/12',
                            'updatedAt': '2026-03-30T00:00:00Z',
                            'author': {'login': 'maintainer'},
                        }
                    ]
                ),
            ),
        }
    )
    monkeypatch.setattr('agent_integrations.github_automation._run_command', stub)

    result = github_issue_list({'limit': 5, 'label': 'bug', 'search': 'federation'}, run_context)

    assert result['count'] == 1
    assert result['issues'][0]['number'] == 12
    assert result['issues'][0]['labels'] == ['bug']
    assert result['issues'][0]['assignees'] == ['alice']



def test_github_issue_prepare_fix_creates_branch_and_task_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_context: RunContext,
) -> None:
    repo_root = tmp_path.resolve()
    run_context.workdir = repo_root
    monkeypatch.setattr('agent_integrations.github_automation.shutil.which', lambda name: 'gh.exe')
    stub = CommandStub(
        {
            ('git', 'rev-parse', '--show-toplevel'): _ok(['git'], stdout=str(repo_root)),
            ('gh', 'auth', 'status'): _ok(['gh']),
            (
                'gh',
                'issue',
                'view',
                '42',
                '--json',
                'number,title,state,body,labels,assignees,url,updatedAt,author,comments',
            ): _ok(
                ['gh'],
                stdout=json.dumps(
                    {
                        'number': 42,
                        'title': 'Replay resilience regression',
                        'state': 'OPEN',
                        'body': 'Please harden replay handling.',
                        'labels': [{'name': 'bug'}, {'name': 'eval'}],
                        'assignees': [{'login': 'alice'}],
                        'url': 'https://example.com/issues/42',
                        'updatedAt': '2026-03-30T00:00:00Z',
                        'author': {'login': 'maintainer'},
                        'comments': [{'author': {'login': 'bob'}, 'body': 'Need tests here.'}],
                    }
                ),
            ),
            ('git', 'branch', '--show-current'): _ok(['git'], stdout='main'),
            ('git', 'show-ref', '--verify', '--quiet', 'refs/heads/issue/42-replay-resilience-regression'): subprocess.CompletedProcess(['git'], 1, stdout='', stderr=''),
            ('git', 'switch', '-c', 'issue/42-replay-resilience-regression'): _ok(['git']),
        }
    )
    monkeypatch.setattr('agent_integrations.github_automation._run_command', stub)

    result = github_issue_prepare_fix({'number': 42}, run_context)

    assert result['branch']['branch_name'] == 'issue/42-replay-resilience-regression'
    assert Path(result['artifacts']['issue_json']).is_file()
    assert Path(result['artifacts']['task_package']).is_file()
    payload = json.loads(Path(result['artifacts']['issue_json']).read_text(encoding='utf-8'))
    assert payload['number'] == 42
    assert 'Repair Checklist' in Path(result['artifacts']['task_package']).read_text(encoding='utf-8')



def test_git_commit_local_requires_staged_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_context: RunContext) -> None:
    repo_root = tmp_path.resolve()
    run_context.workdir = repo_root
    stub = CommandStub(
        {
            ('git', 'rev-parse', '--show-toplevel'): _ok(['git'], stdout=str(repo_root)),
            ('git', 'add', '--', 'README.md'): _ok(['git']),
            ('git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR'): _ok(['git'], stdout=''),
        }
    )
    monkeypatch.setattr('agent_integrations.github_automation._run_command', stub)

    with pytest.raises(GitHubAutomationError, match='No staged changes'):
        git_commit_local({'message': 'feat(test): commit', 'paths': ['README.md']}, run_context)



def test_git_commit_local_returns_commit_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_context: RunContext) -> None:
    repo_root = tmp_path.resolve()
    run_context.workdir = repo_root
    stub = CommandStub(
        {
            ('git', 'rev-parse', '--show-toplevel'): _ok(['git'], stdout=str(repo_root)),
            ('git', 'add', '--all'): _ok(['git']),
            ('git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR'): _ok(['git'], stdout='README.md\nsrc/agent_runtime/public_eval.py\n'),
            ('git', 'commit', '-m', 'feat(runtime): 完成自动化能力'): _ok(['git'], stdout='[main abc123] feat'),
            ('git', 'rev-parse', 'HEAD'): _ok(['git'], stdout='abc123def456\n'),
        }
    )
    monkeypatch.setattr('agent_integrations.github_automation._run_command', stub)

    result = git_commit_local({'message': 'feat(runtime): 完成自动化能力', 'all': True}, run_context)

    assert result['commit_sha'] == 'abc123def456'
    assert result['staged_files'] == ['README.md', 'src/agent_runtime/public_eval.py']



def test_github_release_publish_requires_clean_worktree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_context: RunContext) -> None:
    repo_root = tmp_path.resolve()
    run_context.workdir = repo_root
    monkeypatch.setattr('agent_integrations.github_automation.shutil.which', lambda name: 'gh.exe')
    stub = CommandStub(
        {
            ('git', 'rev-parse', '--show-toplevel'): _ok(['git'], stdout=str(repo_root)),
            ('gh', 'auth', 'status'): _ok(['gh']),
            ('git', 'status', '--porcelain'): _ok(['git'], stdout=' M README.md\n'),
        }
    )
    monkeypatch.setattr('agent_integrations.github_automation._run_command', stub)

    with pytest.raises(GitHubAutomationError, match='clean git worktree'):
        github_release_publish({'tag_name': 'v0.3.3', 'title': 'v0.3.3'}, run_context)



def test_github_release_publish_builds_expected_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_context: RunContext) -> None:
    repo_root = tmp_path.resolve()
    run_context.workdir = repo_root
    notes_file = repo_root / 'release-notes.md'
    notes_file.write_text('notes', encoding='utf-8')
    monkeypatch.setattr('agent_integrations.github_automation.shutil.which', lambda name: 'gh.exe')
    stub = CommandStub(
        {
            ('git', 'rev-parse', '--show-toplevel'): _ok(['git'], stdout=str(repo_root)),
            ('gh', 'auth', 'status'): _ok(['gh']),
            ('git', 'status', '--porcelain'): _ok(['git'], stdout=''),
            ('gh', 'release', 'create', 'v0.3.3', '--title', 'v0.3.3', '--notes-file', 'release-notes.md', '--draft'): _ok(['gh'], stdout='https://example.com/releases/v0.3.3\n'),
        }
    )
    monkeypatch.setattr('agent_integrations.github_automation._run_command', stub)

    result = github_release_publish({'tag_name': 'v0.3.3', 'title': 'v0.3.3', 'notes_file': 'release-notes.md', 'draft': True}, run_context)

    assert result['release_url'] == 'https://example.com/releases/v0.3.3'
