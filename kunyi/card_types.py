"""Card type dataclasses for kunyi.

Each dataclass is a pure data container with no genanki dependency. All
three types share the same optional metadata:

- ``note_id``: stable author-facing identifier (JSON ``id``). Drives the
  note guid when no explicit ``guid`` is given.
- ``guid``: explicit Anki note guid. Used to pin a note to an identity it
  already has inside a user's collection (e.g. when migrating a deck whose
  notes were originally identified by position), so its review history
  survives edits, reordering and renaming of ``note_id``.
- ``concept``: grouping key for cards about one idea (coverage/dedup).
- ``kind``: cognitive task the card trains; one of ``CARD_KINDS``.
- ``source``: citation, rendered as metadata rather than as content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_CLOZE_PATTERN = re.compile(r"\{\{c\d+::.+?\}\}", re.DOTALL)

#: Allowed values for the ``kind`` field (see README, "Card kinds").
CARD_KINDS: frozenset[str] = frozenset(
    {"recall", "explain", "apply", "construct", "compute", "discriminate"}
)


def _check_kind(kind: str | None, owner: str) -> None:
    """Raise if kind is set but not one of CARD_KINDS.

    Parameters
    ----------
    kind:
        Candidate kind value, or None.
    owner:
        Class name used in the error message.
    """
    if kind is not None and kind not in CARD_KINDS:
        raise ValueError(f"{owner}.kind {kind!r} is not one of {sorted(CARD_KINDS)}")


@dataclass
class BasicCard:
    """A two-sided question/answer flashcard.

    Parameters
    ----------
    front:
        Prompt rendered on the question side.
    back:
        Minimum complete answer rendered first on the answer side.
    grading:
        Optional pass criteria shown under the answer, so self-grading is
        consistent from review to review.
    explanation:
        Optional feedback shown after the answer (why it is true, common
        confusions). Not part of what must be recalled.
    tags, media_paths, note_id, guid, concept, kind, source:
        See the module docstring.
    """

    front: str
    back: str
    grading: list[str] = field(default_factory=list)
    explanation: str = ""
    tags: list[str] = field(default_factory=list)
    media_paths: list[Path] = field(default_factory=list)
    note_id: str | None = None
    guid: str | None = None
    concept: str | None = None
    kind: str | None = None
    source: str = ""

    def __post_init__(self) -> None:
        """Validate that front and back are non-empty and kind is known."""
        if not self.front.strip():
            raise ValueError("BasicCard.front cannot be empty")
        if not self.back.strip():
            raise ValueError("BasicCard.back cannot be empty")
        _check_kind(self.kind, "BasicCard")


@dataclass
class MCQCard:
    """A multiple-choice question card.

    Choices are stored in authoring order; the correct answer may sit
    anywhere (LLM output usually puts it first). ``AnkiCardDeck`` shuffles
    the rendered order deterministically per note, so position never
    reveals the key.

    Parameters
    ----------
    question:
        The question stem displayed on the front of the card.
    choices:
        Answer options. Must contain ``correct_answer`` exactly once.
    correct_answer:
        The correct choice string; must be an element of *choices*.
    explanation:
        Why the correct answer is right, shown on the back.
    distractor_notes:
        Optional mapping from a wrong choice to why it is wrong.
    tags, media_paths, note_id, guid, concept, kind, source:
        See the module docstring.
    """

    question: str
    choices: list[str]
    correct_answer: str
    explanation: str
    distractor_notes: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    media_paths: list[Path] = field(default_factory=list)
    note_id: str | None = None
    guid: str | None = None
    concept: str | None = None
    kind: str | None = None
    source: str = ""

    def __post_init__(self) -> None:
        """Validate the question, choices, correct_answer and notes."""
        if not self.question.strip():
            raise ValueError("MCQCard.question cannot be empty")
        if not self.choices:
            raise ValueError("MCQCard.choices cannot be empty")
        if self.correct_answer not in self.choices:
            raise ValueError(
                f"correct_answer {self.correct_answer!r} is not in choices: {self.choices}"
            )
        if self.choices.count(self.correct_answer) != 1:
            raise ValueError("MCQCard.correct_answer appears more than once in choices")
        unknown = set(self.distractor_notes) - set(self.choices)
        if unknown:
            raise ValueError(f"MCQCard.distractor_notes refers to unknown choices: {sorted(unknown)}")
        _check_kind(self.kind, "MCQCard")


@dataclass
class ClozeCard:
    """A cloze-deletion flashcard.

    Parameters
    ----------
    text:
        Card text containing one or more deletions in Anki syntax:
        ``{{c1::answer}}`` or ``{{c1::answer::hint}}``.
    explanation:
        Feedback shown on the back (why the fact holds).
    extra:
        Deprecated alias for *explanation*, kept for v1 JSON files. Used
        only when *explanation* is empty.
    tags, media_paths, note_id, guid, concept, kind, source:
        See the module docstring.
    """

    text: str
    explanation: str = ""
    extra: str = ""
    tags: list[str] = field(default_factory=list)
    media_paths: list[Path] = field(default_factory=list)
    note_id: str | None = None
    guid: str | None = None
    concept: str | None = None
    kind: str | None = None
    source: str = ""

    def __post_init__(self) -> None:
        """Validate that text is non-empty and contains a cloze deletion."""
        if not self.text.strip():
            raise ValueError("ClozeCard.text cannot be empty")
        if not _CLOZE_PATTERN.search(self.text):
            raise ValueError(
                "ClozeCard.text has no cloze deletion "
                f"(expected e.g. '{{{{c1::answer}}}}'): {self.text!r}"
            )
        _check_kind(self.kind, "ClozeCard")
