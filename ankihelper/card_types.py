"""Card type dataclasses for ankihelper.

Each dataclass is a pure data container with no genanki dependency.
Optional fields (tags, media_paths) are reserved for future use and map
directly to genanki.Note(tags=...) and genanki.Package(media_files=...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BasicCard:
    """A simple two-sided flashcard.

    Parameters
    ----------
    front:
        Text rendered on the question side of the card.
    back:
        Text rendered on the answer side of the card.
    tags:
        Optional Anki tags attached to the note (e.g. ["chapter-3", "exam"]).
    media_paths:
        Optional paths to media files (images, audio) referenced by this card.
        Collected at deck-save time and registered with the genanki Package.
    """

    front: str
    back: str
    tags: list[str] = field(default_factory=list)
    media_paths: list[Path] = field(default_factory=list)


@dataclass
class MCQCard:
    """A multiple-choice question card.

    Parameters
    ----------
    question:
        The question stem displayed on the front of the card.
    choices:
        Ordered list of answer options shown below the question.
    correct_answer:
        The correct choice string; must be an element of *choices*.
        Validated on construction so upstream LLM output is caught early.
    explanation:
        Explanation of the correct answer displayed on the card back.
    tags:
        Optional Anki tags attached to the note.
    media_paths:
        Optional paths to media files referenced by this card.
    """

    question: str
    choices: list[str]
    correct_answer: str
    explanation: str
    tags: list[str] = field(default_factory=list)
    media_paths: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate that correct_answer is a member of choices."""
        if self.correct_answer not in self.choices:
            raise ValueError(
                f"correct_answer {self.correct_answer!r} is not in choices: {self.choices}"
            )
