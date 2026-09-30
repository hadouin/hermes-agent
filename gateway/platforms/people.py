"""Read-only, profile-local people.md projection for the HTTP API (no agent work)."""
import re
from hermes_constants import get_hermes_home

_FIELDS = frozenset(('short', 'email', 'telegram', 'phone', 'tz', 'relation'))


def read_people():
    """Return public fields and file mtime in ms; missing file is an empty list."""
    path = get_hermes_home() / 'people.md'
    try:
        with path.open(encoding='utf-8', errors='replace') as file:
            # Read and stat the same descriptor so atomic file replacement is safe.
            import os
            at = os.fstat(file.fileno()).st_mtime_ns // 1_000_000
            people, seen, current = [], set(), None
            for line in file:
                heading = re.fullmatch(r'##\s+(.+?)\s*', line.strip())
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
        return {'people': [], 'at': 0}
