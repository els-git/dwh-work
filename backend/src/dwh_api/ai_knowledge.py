"""Knowledge index of the AI assistant (I17 step 5, I17-08): the user guide cut into sections with access rules.

docs/DWH_USER_GUIDE.md marks every section with `<!-- access: ... -->` (section 0 of the guide):
  all                      any user of the assistant
  permissions=A,B          all listed permissions
  level=ADMIN|SUPERADMIN   account level (ADMIN also accepts SUPERADMIN)
  resource=MODULE:CDP      resource in the user's scope
Rules are joined with ';'. A subsection without its own marker inherits the marker of its section.

A section the user may not read is never given to the model. search() also says whether the best matching
sections were closed to the user, so the assistant can answer "not available to your role" without showing them.
Matching is by word stems: Russian endings are cut (роли, ролям, ролей -> рол), then at most 6 letters are kept;
no external search engine is needed. A question in another language is translated by the model first (ai_assistant).
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
# Frequent words that say nothing about the subject of a question (compared after stemming).
STOP_WORDS = ("как", "что", "где", "для", "это", "или", "при", "все", "так", "его", "она", "они", "чем", "если",
              "the", "and", "how", "what", "where", "можно", "нужно", "какой", "какие", "почему", "сделать",
              "дай", "мне", "про", "расскажи", "покажи", "информация", "скажи")
# Russian endings, longest first; the stem keeps at least 3 letters.
ENDINGS = tuple(sorted((
    "иями", "ями", "ами", "иях", "ием", "ией", "ии", "ого", "его", "ому", "ему", "ыми", "ими", "ях", "ах", "ов", "ев",
    "ей", "ой", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ию", "ия", "ам", "ям", "ом", "ем",
    "ью", "ть", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й"), key=len, reverse=True))
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


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
    # Sections close to the best match that the role may not read, while open ones were found too: the model
    # is told that part of the subject is closed (without their text or titles), so it does not answer
    # "there is nothing in the guide" about a subject described for another role.
    closed_nearby: int = 0

    @property
    def only_denied(self) -> bool:
        return not self.sections and bool(self.denied_titles)


def stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    if CYRILLIC_RE.search(word):
        for ending in ENDINGS:
            if word.endswith(ending) and len(word) - len(ending) >= 3:
                word = word[: -len(ending)]
                break
    return word[:6]


STOP = frozenset(stem(w) for w in STOP_WORDS)


def stems(text: str) -> frozenset[str]:
    return frozenset(s for s in (stem(w) for w in WORD_RE.findall(text)) if s not in STOP)


def is_russian(text: str) -> bool:
    """The question is in Russian when Cyrillic letters are at least half of all letters."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if CYRILLIC_RE.match(c)) * 2 >= len(letters)


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

    def screen_map(self, user: UserScope, chars: int = 160) -> str:
        """Every section the user may read: title and the start of its text (for questions with a screenshot)."""
        lines = []
        for section in self.sections:
            if section.access.allows(user):
                body = " ".join(section.text.split("\n", 1)[1:]).replace("\n", " ").strip()
                lines.append(f"- {section.title}: {body[:chars]}")
        return "\n".join(lines)

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
        return SearchResult(allowed, denied if not allowed else [], len(denied) if allowed else 0)
