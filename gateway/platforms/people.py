"""Read-only, profile-local people projection for the HTTP API (no agent work)."""
from contextlib import ExitStack
import errno
import os
import re
import stat

import yaml

from hermes_constants import get_hermes_home

_FIELDS = frozenset(('short', 'email', 'telegram', 'phone', 'tz', 'relation'))


def read_people():
    """Prefer people.md; otherwise project the active profile's KB frontmatter."""
    home = get_hermes_home()
    path = home / 'people.md'
    try:
        with path.open(encoding='utf-8', errors='replace') as file:
            # Read and stat the same descriptor so atomic file replacement is safe.
            at = os.fstat(file.fileno()).st_mtime_ns // 1_000_000
            people, seen, current = [], set(), None
            fence = None
            for line in file:
                stripped = line.strip()
                if fence is not None:
                    # Only the matching marker, at least as long, closes a fence.
                    if re.fullmatch(re.escape(fence[0]) + r'{' + str(len(fence)) + r',}', stripped):
                        fence = None
                    continue
                marker = re.match(r'(`{3,}|~{3,})', stripped)
                if marker:
                    # Examples in notes are not people or public contact fields.
                    fence = marker[1]
                    continue
                heading = re.fullmatch(r'##\s+(.+?)\s*', stripped)
                if heading:
                    name = heading[1].strip()
                    current = None
                    if name and name not in seen and len(people) < 500:
                        seen.add(name)
                        current = {'name': name}
                        people.append(current)
                    continue
                if line.lstrip().startswith('#'):
                    current = None
                if current is None:
                    continue
                field = re.fullmatch(r'\s*-\s*([a-z]+):\s*(.*?)\s*', line)
                if not field:
                    continue
                key, value = field.groups()
                if not value or key in current:
                    continue
                if key == 'aliases':
                    current[key] = [a.strip() for a in value.split(',') if a.strip()]
                elif key in _FIELDS:
                    current[key] = value
        return {'people': people, 'at': at}
    except FileNotFoundError:
        return _read_people_kb(home)


# Only these explicit header keys participate in the public projection. In
# particular, sources, body text, IDs and arbitrary nested notes are never searched.
_KB_KEYS = _FIELDS | frozenset((
    'person', 'name', 'names', 'aliases', 'relationship', 'identifiers',
    'contact_ids', 'google_contact_identifiers', 'emails', 'phones',
    'telegrams', 'telegram_id', 'telegram_ids',
))
_CONTACT_KEYS = {
    'email': ('email', 'emails'),
    'phone': ('phone', 'phones'),
    'telegram': ('telegram', 'telegrams', 'telegram_id', 'telegram_ids'),
}
_HEADER_LIMIT = 64 * 1024


def _values(value):
    """Flat, explicit scalar values only; never stringify containers or booleans."""
    values = value if isinstance(value, list) else [value]
    return [str(v).strip() for v in values
            if type(v) in (str, int) and str(v).strip()]


def _frontmatter(file):
    """Read a bounded, closed header only; safely recover bad private metadata."""
    if file.readline(_HEADER_LIMIT + 1).strip() != '---':
        return None
    lines, size = [], 0
    for _ in range(2048):
        line = file.readline(_HEADER_LIMIT + 1)
        size += len(line)
        if not line or size > _HEADER_LIMIT:
            return None
        if line.strip() == '---':
            break
        lines.append(line)
    else:
        return None
    try:
        data = yaml.safe_load(''.join(lines))
    except yaml.YAMLError:
        # Actual KB source lists sometimes contain unquoted colons. Retry only
        # complete allowlisted top-level blocks, preserving their indentation.
        # Bad public blocks still fail; never salvage individual scalar lines.
        selected, keep = [], False
        for line in lines:
            if line.strip() and not line.startswith((' ', '\t', '#')):
                key = re.match(r'([a-z_]+):(?:\s|$)', line)
                keep = bool(key and key[1] in _KB_KEYS)
            if keep:
                selected.append(line)
        try:
            data = yaml.safe_load(''.join(selected))
        except (yaml.YAMLError, RecursionError):
            return None
    except RecursionError:
        return None
    return data if isinstance(data, dict) else None


def _project_person(data):
    names = _values(data.get('person')) or _values(data.get('name')) or _values(data.get('names'))
    if not names:
        return None
    person: dict[str, str | list[str]] = {'name': names[0]}
    aliases = _values(data.get('aliases')) + _values(data.get('names'))
    aliases = list(dict.fromkeys(a for a in aliases if a != person['name']))
    if aliases:
        person['aliases'] = aliases
    for key in ('short', 'tz', 'relation'):
        values = _values(data.get(key))
        if key == 'relation' and not values:
            values = _values(data.get('relationship'))
        if values:
            person[key] = values[0]
    # Retain the singular API contract: first explicit value, no phone inferred
    # from WhatsApp IDs, no guessed short names/timezones, no mask expansion.
    containers = [data] + [data[key] for key in (
        'identifiers', 'contact_ids', 'google_contact_identifiers')
        if isinstance(data.get(key), dict)]
    for field, keys in _CONTACT_KEYS.items():
        values = [v for container in containers for key in keys
                  for v in _values(container.get(key))]
        if values:
            person[field] = values[0]
    return person


def _read_people_kb(home):
    """Sorted local KB files, no symlinks; directory and all file mtimes in ms."""
    people, seen, at = [], set(), 0
    with ExitStack() as stack:
        # Descriptor-relative traversal pins every directory against symlink
        # replacement. No external KB environment override or cross-profile path.
        try:
            directory = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, directory)
            for component in ('people-kb', 'people'):
                directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                stack.callback(os.close, directory)
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                return {'people': [], 'at': 0}
            raise
        at = os.fstat(directory).st_mtime_ns
        with os.scandir(directory) as entries:
            filenames = sorted(entry.name for entry in entries
                               if entry.name.endswith('.md') and entry.is_file(follow_symlinks=False))
        for filename in filenames:
            try:
                fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
            except OSError as error:
                if error.errno in (errno.ENOENT, errno.ELOOP):
                    continue
                raise
            with os.fdopen(fd, encoding='utf-8', errors='replace') as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode):
                    continue
                at = max(at, info.st_mtime_ns)
                if len(people) >= 500:
                    continue
                data = _frontmatter(file)
                person = _project_person(data) if data else None
                if person and person['name'] not in seen:
                    seen.add(person['name'])
                    people.append(person)
        at = max(at, os.fstat(directory).st_mtime_ns)
    return {'people': people, 'at': at // 1_000_000}
