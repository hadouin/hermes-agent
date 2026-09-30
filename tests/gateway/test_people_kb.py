"""Synthetic versions of the actual KB schemas; no private KB data copied."""
import os
import sys

import pytest

from gateway.platforms.people import read_people


@pytest.fixture
def kb(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    directory = tmp_path / 'people-kb' / 'people'
    directory.mkdir(parents=True)
    return directory


def note(kb, filename, header, body='PRIVATE BODY\n- email: body-secret@example.com'):
    path = kb / filename
    path.write_text('---\n' + header + '\n---\n' + body, encoding='utf-8')
    return path


def test_missing_people_md_uses_actual_kb_schema(kb):
    path = note(kb, 'a.md', '''person: Zoë Example
short: Z
aliases: [Zee, Zo]
identifiers:
  emails: [zoe@example.com, other@example.com]
  phones: ['+33 ** ** 12', '+33123456789']
  telegram: ['@zoe']
  whatsapp_ids: ['999@s.whatsapp.net']
relationship: friend
google_contact_sources:
  - account: PRIVATE SOURCE
google_contact_identifiers:
  phones: ['+33987654321']
projects: [PRIVATE PROJECT]
notes: PRIVATE NOTE''')
    os.utime(kb, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    os.utime(path, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
    assert read_people() == {'people': [{
        'name': 'Zoë Example', 'short': 'Z', 'aliases': ['Zee', 'Zo'],
        'email': 'zoe@example.com', 'phone': '+33 ** ** 12',
        'telegram': '@zoe', 'relation': 'friend',
    }], 'at': 1_700_000_000_123}


def test_people_md_preferred_even_when_empty(kb):
    note(kb, 'a.md', 'person: Fallback')
    path = kb.parent.parent / 'people.md'
    path.write_text('')
    assert read_people() == {'people': [], 'at': path.stat().st_mtime_ns // 1_000_000}
    path.write_text('## Original\n- aliases: A, B\n- phone: +33 **\n')
    assert read_people()['people'] == [{'name': 'Original', 'aliases': ['A', 'B'], 'phone': '+33 **'}]


def test_missing_kb_ignores_external_environment_paths(tmp_path, monkeypatch):
    external = tmp_path / 'external'
    external.mkdir()
    note(external, 'a.md', 'person: Wrong Profile')
    profile = tmp_path / 'profile'
    profile.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(profile))
    for key in ('PEOPLE_KB', 'PEOPLE_KB_PATH', 'HERMES_PEOPLE_KB', 'HERMES_PEOPLE_PATH'):
        monkeypatch.setenv(key, str(external))
    assert read_people() == {'people': [], 'at': 0}
    (profile / 'people-kb').mkdir()
    assert read_people() == {'people': [], 'at': 0}


def test_alternate_explicit_names_and_contacts(kb):
    note(kb, 'a.md', '''id: private-id
names: [Full Name, Other Name]
contact_ids:
  email: [full@example.com]
  phone: ['+44 ** 12']
  telegram: ['@full']
  whatsapp: ['888@s.whatsapp.net']
relationship: collaborator''')
    note(kb, 'b.md', '''name: Name Only
aliases: [Alias]
identifier: private-ambiguous-id
organization: PRIVATE
source: PRIVATE''')
    note(kb, 'c.md', '''person: Google Only
google_contact_identifiers:
  emails: [google@example.com]
  phones: ['+49 ** 34']''')
    note(kb, 'd.md', 'person: 123456')
    assert read_people()['people'] == [
        {'name': 'Full Name', 'aliases': ['Other Name'], 'email': 'full@example.com',
         'phone': '+44 ** 12', 'telegram': '@full', 'relation': 'collaborator'},
        {'name': 'Name Only', 'aliases': ['Alias']},
        {'name': 'Google Only', 'email': 'google@example.com', 'phone': '+49 ** 34'},
        {'name': '123456'},
    ]


def test_malformed_private_sources_can_be_ignored(kb):
    note(kb, 'a.md', '''person: Recoverable
identifiers:
  emails: [recoverable@example.com]
source_account: PRIVATE
sources:
  - Gmail search: from: bad: yaml''')
    assert read_people()['people'] == [{'name': 'Recoverable', 'email': 'recoverable@example.com'}]


@pytest.mark.parametrize('header', [
    'person: [broken', 'person: !!python/object/apply:os.system [false]',
    'person: {notes: PRIVATE}', 'person: true', 'aliases: [No Name]',
    'person: Bad\nidentifiers: [broken', 'person: Bad\nemail: !!unknown PRIVATE',
])
def test_invalid_entries_are_skipped(kb, header):
    note(kb, 'a.md', header)
    note(kb, 'z.md', 'person: Valid')
    assert read_people()['people'] == [{'name': 'Valid'}]


def test_no_body_or_nested_notes_leakage(kb):
    note(kb, 'a.md', '''person: Safe
email: {note: PRIVATE}
aliases: [Public, {note: PRIVATE}, false]
identifiers:
  note: PRIVATE
  sources:
    email: private@example.com
  whatsapp_ids: ['123@s.whatsapp.net']''', '## Private\nshort: PRIVATE\nemail: private@example.com')
    (kb / 'b.md').write_text('person: Not Frontmatter\n---\n')
    (kb / 'c.md').write_text('---\nperson: Unclosed\n')
    assert read_people()['people'] == [{'name': 'Safe', 'aliases': ['Public']}]


def test_kb_mtime_includes_directory_and_all_markdown_files(kb):
    a = note(kb, 'a.md', 'person: Valid')
    b = note(kb, 'b.md', 'person: [malformed')
    ignored = kb / 'ignored.txt'
    ignored.write_text('irrelevant')
    for path, seconds in ((kb, 100), (a, 200), (b, 300), (ignored, 400)):
        os.utime(path, ns=(seconds * 1_000_000_000, seconds * 1_000_000_000))
    assert read_people()['at'] == 300_000
    b.unlink()
    os.utime(kb, ns=(500_000_000_000, 500_000_000_000))
    assert read_people()['at'] == 500_000


def test_empty_existing_kb_has_directory_mtime(kb):
    assert read_people() == {'people': [], 'at': kb.stat().st_mtime_ns // 1_000_000}


@pytest.mark.parametrize('header', [
    'person: Oversized\nnotes: ' + 'x' * (64 * 1024),
    'person: Too Many Lines\n' + '# comment\n' * 2048,
    'person: Recursive\naliases: &loop [*loop]',
])
def test_bounded_headers_and_recursive_aliases(kb, header):
    note(kb, 'a.md', header)
    result = read_people()['people']
    assert result == ([{'name': 'Recursive'}] if 'Recursive' in header else [])


@pytest.mark.asyncio
async def test_kb_served_over_authenticated_http_without_agent(kb):
    from aiohttp import ClientSession
    from unittest.mock import Mock
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    note(kb, 'a.md', 'person: API Example\nidentifiers:\n  emails: [api@example.com]')
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
        'key': 'synthetic-test-key', 'host': '127.0.0.1', 'port': 0}))
    adapter._create_agent = Mock(side_effect=AssertionError('No model allowed'))
    assert await adapter.connect()
    port = adapter._site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as client:
            url = f'http://127.0.0.1:{port}/v1/people'
            async with client.get(url) as response:
                assert response.status == 401
            async with client.get(url, headers={'Authorization': 'Bearer synthetic-test-key'}) as response:
                assert response.status == 200
                assert await response.json() == read_people()
        adapter._create_agent.assert_not_called()
    finally:
        await adapter.disconnect()


@pytest.mark.skipif(sys.platform == 'win32', reason='Symlinks require elevated privileges')
def test_symlink_files_and_directories_excluded(kb, tmp_path):
    outside = tmp_path / 'outside.md'
    outside.write_text('---\nperson: Outside\n---\n')
    note(kb, 'a.md', 'person: Inside')
    (kb / 'b.md').symlink_to(outside)
    (kb / 'c.md').symlink_to(kb / 'a.md')
    (kb / 'd.md').symlink_to(tmp_path / 'absent')
    assert read_people()['people'] == [{'name': 'Inside'}]
    for entry in kb.iterdir():
        entry.unlink()
    kb.rmdir()
    kb.symlink_to(tmp_path, target_is_directory=True)
    assert read_people() == {'people': [], 'at': 0}
    kb.unlink()
    kb.parent.rmdir()
    kb.parent.symlink_to(tmp_path, target_is_directory=True)
    assert read_people() == {'people': [], 'at': 0}


def test_cap_exact_duplicates_first_stable_filename_order(kb):
    for i in reversed(range(510)):
        note(kb, f'{i:04}.md', f'person: Person {i:04}')
    note(kb, '0000-duplicate.md', 'person: Person 0000\nemail: first@example.com')
    note(kb, '0001-case.md', 'person: person 0000')
    last = kb / '0509.md'
    os.utime(last, ns=(2_000_000_000_123_000_000, 2_000_000_000_123_000_000))
    result = read_people()
    assert len(result['people']) == 500
    assert result['people'][0] == {'name': 'Person 0000', 'email': 'first@example.com'}
    assert result['people'][1] == {'name': 'person 0000'}
    assert result['people'][-1] == {'name': 'Person 0498'}
    assert result['at'] == 2_000_000_000_123
