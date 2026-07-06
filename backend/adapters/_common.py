import os
import subprocess
import re
from datetime import datetime

TICKET_RE = re.compile(r'\b([A-Z]{2,10}-\d+)\b|#(\d+)\b')
# Common technical abbreviations that match the ticket-key shape
# ([A-Z]{2,10}-\d+) but are never real ticket prefixes, e.g. "UTF-8",
# "SHA-256", "ISO-8601" — these show up constantly in commit messages
# and code discussion and would otherwise render as bogus ticket refs.
_NON_TICKET_PREFIXES = frozenset({
    "UTF", "SHA", "HTTP", "HTTPS", "ISO", "RFC", "MD", "CRC", "JSON", "XML",
    "HTML", "CSS", "URL", "URI", "API", "SQL", "CPU", "GPU", "RAM", "TCP",
    "UDP", "DNS", "CDN", "JWT", "CORS", "REST", "AES", "RSA", "SSH", "SSL",
    "TLS", "IPV", "USB", "PDF", "CSV", "YAML", "TOML", "GRPC", "OAUTH",
    "ASCII", "UUID", "GUID", "IP", "OS", "IO", "ID",
})
GIT_COMMIT_RE = re.compile(r'\bgit commit\b')
GH_PR_RE = re.compile(r'\bgh pr create\b')
GIT_PUSH_RE = re.compile(r'\bgit push\b')
GIT_REMOTE_RE = re.compile(r'\bgit remote\b')
COMMIT_HASH_RE = re.compile(r'\[[\w/._-]+ ([0-9a-f]{7,40})\]')
PR_URL_RE = re.compile(r'https://github\.com/\S+/pull/\d+')
GITHUB_REPO_RE = re.compile(r'(?:https://github\.com/|github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?(?:[/\s]|$)')


def _parse_ts(ts_str: str) -> datetime:
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _extract_tickets(text: str) -> list[str]:
    refs = []
    for m in TICKET_RE.finditer(text):
        if m.group(1):
            if m.group(1).split('-')[0] in _NON_TICKET_PREFIXES:
                continue
            ref = m.group(1)
        else:
            ref = f"#{m.group(2)}"
        if ref not in refs:
            refs.append(ref)
    return refs


def _get_user() -> str:
    try:
        result = subprocess.run(
            ['git', 'config', 'user.name'],
            capture_output=True, text=True, timeout=2
        )
        name = result.stdout.strip()
        if name:
            return name
    except Exception:
        pass
    return os.environ.get('USER', 'unknown')
