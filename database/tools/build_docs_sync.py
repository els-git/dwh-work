"""Build database/sync_docs_to_pc.ps1: embeds the current project documents and
lists every SHA256 they ever had in git history, so the PC script replaces only
known versions and stops on local edits. Run from the repository root after
committing document changes: python3 database/tools/build_docs_sync.py
"""
import base64
import hashlib
import subprocess
from pathlib import Path

DOCS = [
    'docs/DWH_IMPLEMENTATION_ROADMAP.md',
    'docs/DWH_CURRENT_FUNCTIONALITY.md',
    'docs/DWH_I02_04_MIGRATION_TEST_ROLLBACK_PLAN_APPROVED_20261103.md',
    'CLAUDE.md',
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def history(path: str) -> list[str]:
    commits = subprocess.run(['git', 'log', '--all', '--format=%H', '--', path],
                             capture_output=True, text=True, check=True).stdout.split()
    found = []
    for c in commits:
        r = subprocess.run(['git', 'show', f'{c}:{path}'], capture_output=True)
        if r.returncode == 0:
            found.append(sha(r.stdout))
    return found


def main() -> None:
    entries = []
    for path in DOCS:
        data = Path(path).read_bytes()
        known = sorted(set(history(path)) - {sha(data)})
        known_ps = ', '.join(f"'{k}'" for k in known) or ''
        entries.append(
            "    @{\n"
            f"        Path    = '{path.replace('/', chr(92))}'\n"
            f"        Sha     = '{sha(data)}'\n"
            f"        Known   = @({known_ps})\n"
            "        Current = ''\n"
            f"        Data    = '{base64.b64encode(data).decode()}'\n"
            "    }")
    template = Path('database/tools/sync_docs_to_pc.template.ps1').read_text()
    out = template.replace('@@FILES@@', ',\n'.join(entries)).replace('\n', '\r\n')
    Path('database/sync_docs_to_pc.ps1').write_bytes(out.encode('ascii'))
    for path in DOCS:
        print(sha(Path(path).read_bytes())[:16], path)


if __name__ == '__main__':
    main()
