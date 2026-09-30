"""Real authenticated HTTP tests: people are file data, never agent work."""
import os
from unittest.mock import Mock

from gateway.platforms.people import read_people

import pytest
from aiohttp import ClientSession
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


@pytest.mark.asyncio
async def test_people_http_contract(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
        'key': 'people-test-secret', 'host': '127.0.0.1', 'port': 0}))
    adapter._create_agent = Mock(side_effect=AssertionError('No model allowed'))
    assert await adapter.connect()
    port = adapter._site._server.sockets[0].getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    headers = {'Authorization': 'Bearer people-test-secret'}
    try:
        async with ClientSession() as client:
            async with client.get(url+'/v1/people', headers=headers) as r:
                assert r.status == 200
                assert await r.json() == {'people': [], 'at': 0}
            file = tmp_path/'people.md'
            file.write_text('''# People\n\n## Jilles Soeters\n- short: Jilles\n\n- aliases: JS, Jill, ,\n- telegram: @jilles\n- email: jilles@example.com\n- phone: +31 6\n- tz: Europe/Amsterdam\n- relation: work\n- note: PRIVATE\n- secret: NEVER\n### Notes\n- email: must-not-override@example.com\n## Jilles Soeters\n- email: duplicate@example.com\n## Maman\n- aliases: mum, mom\n''')
            async with client.get(url+'/v1/people?file=/etc/passwd', headers=headers) as r:
                assert r.status == 200
                body = await r.json()
                assert body == {'people': [
                    {'name': 'Jilles Soeters', 'short': 'Jilles', 'aliases': ['JS', 'Jill'],
                     'telegram': '@jilles', 'email': 'jilles@example.com', 'phone': '+31 6',
                     'tz': 'Europe/Amsterdam', 'relation': 'work'},
                    {'name': 'Maman', 'aliases': ['mum', 'mom']}],
                    'at': file.stat().st_mtime_ns // 1_000_000}
            for h in [{}, {'Authorization': 'Bearer wrong'}]:
                async with client.get(url+'/v1/people', headers=h) as r:
                    assert r.status == 401
            for method in ['POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']:
                async with client.request(method, url+'/v1/people', headers=headers) as r:
                    assert r.status == 405
            async with client.get(url+'/v1/capabilities', headers=headers) as r:
                body = await r.json()
                assert body['features']['people_api'] is True
                assert body['endpoints']['people'] == {'method': 'GET', 'path': '/v1/people'}
            file.write_text(''.join(f'## Person {i}\n- note: private\n' for i in range(510)))
            async with client.get(url+'/v1/people', headers=headers) as r:
                assert len((await r.json())['people']) == 500
            file.unlink()
            async with client.get(url+'/v1/people', headers=headers) as r:
                assert await r.json() == {'people': [], 'at': 0}
            adapter._api_key = ''
            async with client.get(url+'/v1/people') as r:
                assert r.status == 401
            adapter._create_agent.assert_not_called()
    finally:
        await adapter.disconnect()


@pytest.mark.parametrize(('text', 'expected'), [
    ('', []),
    ('# People\n- email: ignored@example.com\n## Name Only\n', [{'name': 'Name Only'}]),
    ('  ##  Zoë Dupont  \n\n  - short:  Zoë  \n- aliases: Z, , Zee,\n'
     '- telegram: @zoe\n- email: \n- unknown: ignored\n- note: private\n',
     [{'name': 'Zoë Dupont', 'short': 'Zoë', 'aliases': ['Z', 'Zee'], 'telegram': '@zoe'}]),
    ('## First\n- email: first@example.com\n- email: second@example.com\n'
     '## First\n- phone: ignored\n## Second\n- relation: friend\n',
     [{'name': 'First', 'email': 'first@example.com'}, {'name': 'Second', 'relation': 'friend'}]),
    ('## Person\n- note: secret\n### Private\n- phone: secret\n'
     '# Other\n- email: secret\n## Next\n', [{'name': 'Person'}, {'name': 'Next'}]),
    ('## Person\n- aliases: \n- tz: \n- phone: \n', [{'name': 'Person'}]),
    ('## Compact\n-short:C\n-telegram:@compact\n',
     [{'name': 'Compact', 'short': 'C', 'telegram': '@compact'}]),
])
def test_people_parser(tmp_path, monkeypatch, text, expected):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    path = tmp_path / 'people.md'
    path.write_text(text, encoding='utf-8')
    os.utime(path, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert read_people() == {'people': expected, 'at': path.stat().st_mtime_ns // 1_000_000}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_people_profile_path_and_cap(tmp_path, monkeypatch):
    profile = tmp_path / 'profiles' / 'test'
    profile.mkdir(parents=True)
    (tmp_path / 'people.md').write_text('## Wrong Profile\n')
    path = profile / 'people.md'
    path.write_text('## First\n## First\n' + ''.join(
        f'## Person {i}\n- short: P{i}\n' for i in range(510)))
    monkeypatch.setenv('HERMES_HOME', str(profile))
    people = read_people()['people']
    assert len(people) == 500
    assert people[0] == {'name': 'First'}
    assert people[-1] == {'name': 'Person 498', 'short': 'P498'}


@pytest.mark.asyncio
async def test_people_read_error_is_private_and_auth_precedes_read(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    reader = Mock(side_effect=PermissionError('private filesystem details'))
    monkeypatch.setattr('gateway.platforms.people.read_people', reader)
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
        'key': 'people-test-secret', 'host': '127.0.0.1', 'port': 0}))
    assert await adapter.connect()
    port = adapter._site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as client:
            url = f'http://127.0.0.1:{port}/v1/people'
            async with client.get(url) as response:
                assert response.status == 401
                reader.assert_not_called()
            async with client.get(url, headers={'Authorization': 'Bearer people-test-secret'}) as response:
                assert response.status == 503
                assert await response.json() == {'error': {'message': 'People file unavailable'}}
            reader.assert_called_once_with()
    finally:
        await adapter.disconnect()
