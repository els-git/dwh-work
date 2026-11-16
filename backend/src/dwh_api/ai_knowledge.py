"""Knowledge index of the AI assistant (I17 step 5, I17-08): the user guide cut into sections with access rules.

docs/DWH_USER_GUIDE.md marks every section with `<!-- access: ... -->` (section 0 of the guide):
  all                      any user of the assistant
  permissions=A,B          all listed permissions
  level=ADMIN|SUPERADMIN   account level (ADMIN also accepts SUPERADMIN)
  resource=MODULE:CDP      resource in the user's scope
Rules are joined with ';'. A subsection without its own marker inherits the marker of its section.

A section the user may not read is never given to the model. search() also says whether the best matching
sections were closed to the user, so the assistant can answer "not available to your role" without showing them.
Matching is by word stems (first 6 letters), as in the probe of step 2: no external search engine is needed.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

GUIDE_PATH = Path(__file__).resolve().parents[3] / "docs" / "DWH_USER_GUIDE.md"
MARKER_RE = re.compile(r"^<!-- access: (.+) -->\s*$")
HEADING_RE = re.compile(r"^(#{2,3}) (.+)$")
WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁёƏəĞğİıÖöŞşÜüÇç_]{3,}")
LEVELS = {"ADMIN": ("ADMIN", "SUPERADMIN"), "SUPERADMIN": ("SUPERADMIN",)}
# Frequent words that say nothing about the subject of a question.
STOP = {"как", "что", "где", "для", "это", "или", "при", "все", "так", "его", "она", "они", "чем", "есл",
        "the", "and", "how", "what", "where", "можно", "нужно", "какой", "какие", "почему", "сделат"}


class GuideError(ValueError):
    pass


@dataclass(frozen=True)
class Access:
    permissions: frozenset[str] = frozenset()
    level: str | None = None
    resources: frozenset[str] = frozenset()

    @classmethod
    def parse(cls, value: str) -> "Access":
        value = value.strip()
        if value == "all":
            return cls()
        permissions: set[str] = set()
        resources: set[str] = set()
        level = None
        for part in value.split(";"):
            key, sep, items = part.strip().partition("=")
            values = [v.strip() for v in items.split(",") if v.strip()]
            if not sep or not values:
                raise GuideError(f"bad access marker: {value}")
            if key == "permissions":
                permissions.update(values)
            elif key == "level" and len(values) == 1 and values[0] in LEVELS:
                level = values[0]
            elif key == "resource":
                resources.update(values)
            else:
                raise GuideError(f"bad access marker: {value}")
        return cls(frozenset(permissions), level, frozenset(resources))

    def allows(self, user: "UserScope") -> bool:
        if not self.permissions <= user.permissions:
            return False
        if self.level is not None and user.account_level not in LEVELS[self.level]:
            return False
        return self.resources <= user.resources


@dataclass(frozen=True)
class UserScope:
    """What the server knows about the session user (from app.auth_get_* via the API session, never the client)."""
    account_level: str
    permissions: frozenset[str]
    resources: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Section:
    number: str
    title: str
    text: str
    access: Access
    words: frozenset[str] = field(default_factory=frozenset)
    title_words: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SearchResult:
    sections: list[Section]
    denied_titles: list[str]

    @property
    def only_denied(self) -> bool:
        return not self.sections and bool(self.denied_titles)


def stems(text: str) -> frozenset[str]:
    return frozenset(w.lower()[:6] for w in WORD_RE.findall(text) if w.lower()[:6] not in STOP)


def parse_guide(text: str) -> list[Section]:
    sections: list[Section] = []
    lines = text.splitlines()
    top_access: Access | None = None
    current: dict | None = None

    def close() -> None:
        if current is not None:
            body = "\n".join(current["lines"]).strip()
            sections.append(Section(current["number"], current["title"], body, current["access"],
                                    stems(current["title"] + "\n" + body), stems(current["title"])))

    for i, line in enumerate(lines):
        heading = HEADING_RE.match(line)
        if not heading:
            if current is not None and not MARKER_RE.match(line):
                current["lines"].append(line)
            continue
        close()
        level, title = heading.groups()
        marker = MARKER_RE.match(lines[i + 1]) if i + 1 < len(lines) else None
        if level == "##":
            if marker is None:
                raise GuideError(f"section without access marker: {title}")
            top_access = Access.parse(marker.group(1))
            access = top_access
        else:
            if top_access is None:
                raise GuideError(f"subsection outside a section: {title}")
            access = Access.parse(marker.group(1)) if marker else top_access
        number = title.split(" ", 1)[0].rstrip(".")
        current = {"number": number, "title": title, "access": access, "lines": [title]}
    close()
    if not sections:
        raise GuideError("the guide has no sections")
    return sections


class KnowledgeIndex:
    def __init__(self, sections: Iterable[Section]):
        self.sections = [s for s in sections if not s.number.startswith("0")]  # section 0 is for the assistant
        # Rare words say more about a section than frequent ones (inverse document frequency).
        total = len(self.sections) or 1
        counts: dict[str, int] = {}
        for section in self.sections:
            for word in section.words:
                counts[word] = counts.get(word, 0) + 1
        self.weight = {w: math.log(1 + total / c) for w, c in counts.items()}

    def score(self, wanted: frozenset[str], section: Section) -> float:
        return sum(self.weight.get(w, 0.0) * (2.0 if w in section.title_words else 1.0) for w in wanted & section.words)

    @classmethod
    def from_file(cls, path: Path = GUIDE_PATH) -> "KnowledgeIndex":
        return cls(parse_guide(path.read_text(encoding="utf-8")))

    def search(self, question: str, user: UserScope, top: int = 4) -> SearchResult:
        wanted = stems(question)
        scored = sorted(((self.score(wanted, s), s) for s in self.sections), key=lambda x: x[0], reverse=True)
        scored = [(score, s) for score, s in scored if score > 0]
        if not scored:
            return SearchResult([], [])
        best = scored[0][0]
        # Sections close to the best match: the denied ones among them explain a refusal.
        near = [(score, s) for score, s in scored if score >= best * 0.6]
        allowed = [s for _, s in scored if s.access.allows(user)][:top]
        denied = [s.title for _, s in near if not s.access.allows(user)]
        if allowed and not any(s.access.allows(user) for _, s in near):
            # The question is about a closed subject; loosely related open sections would mislead.
            allowed = []
        return SearchResult(allowed, denied if not allowed else [])
