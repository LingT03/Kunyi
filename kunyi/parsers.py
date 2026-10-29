"""Input parsers for kunyi.

Each parser reads a file and returns a list of typed card objects.
Supported formats:
  - JSON: QA, MCQ and cloze cards, schema v1 or v2 (see README)
  - TSV:  BasicCard list with columns front<TAB>back (optional header row)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Union

from kunyi.card_types import BasicCard, ClozeCard, MCQCard


RETIRED_TAG = "kunyi::retired"

_Card = Union[BasicCard, MCQCard, ClozeCard]


def _common(entry: dict[str, Any]) -> dict[str, Any]:
    """Extract the metadata keys shared by every card type.

    Parameters
    ----------
    entry:
        One card object from the JSON file.

    Returns
    -------
    dict[str, Any]
        Keyword arguments for the card dataclass constructors.
    """
    return {
        "tags": list(entry.get("tags", [])),
        "note_id": entry.get("id"),
        "guid": entry.get("guid"),
        "concept": entry.get("concept"),
        "kind": entry.get("kind"),
        "source": entry.get("source", ""),
    }


def _parse_entry(entry: dict[str, Any]) -> _Card:
    """Build one card object from a JSON entry (schema v1 or v2).

    Parameters
    ----------
    entry:
        One card object. ``type`` defaults to ``"mcq"`` for v1 files.

    Returns
    -------
    BasicCard | MCQCard | ClozeCard
        The validated card.

    Raises
    ------
    KeyError
        If a required field is missing.
    ValueError
        If ``type`` is unknown or the dataclass validation fails.
    """
    card_type = entry.get("type", "mcq")
    common = _common(entry)
    if card_type == "mcq":
        question = entry.get("question", entry.get("prompt"))
        if question is None:
            raise KeyError("question")
        if "answer" in entry and "distractors" in entry:
            # v2: key and distractors stored apart, so authoring order can
            # never leak the answer; rendering shuffles them together.
            distractors = entry["distractors"]
            texts = [d["text"] if isinstance(d, dict) else d for d in distractors]
            notes = {
                d["text"]: d["why_wrong"]
                for d in distractors
                if isinstance(d, dict) and d.get("why_wrong")
            }
            return MCQCard(
                question=question,
                choices=[entry["answer"], *texts],
                correct_answer=entry["answer"],
                explanation=entry["explanation"],
                distractor_notes=notes,
                **common,
            )
        return MCQCard(
            question=question,
            choices=entry["choices"],
            correct_answer=entry["correct_answer"],
            explanation=entry["explanation"],
            **common,
        )
    if card_type == "cloze":
        return ClozeCard(
            text=entry["text"],
            explanation=entry.get("explanation", ""),
            extra=entry.get("extra", ""),
            **common,
        )
    if card_type in {"qa", "basic"}:
        return BasicCard(
            front=entry.get("prompt", entry.get("front", "")),
            back=entry.get("answer", entry.get("back", "")),
            grading=list(entry.get("grading", [])),
            explanation=entry.get("explanation", ""),
            **common,
        )
    raise ValueError(f"Unknown card type {card_type!r} (expected 'qa', 'mcq' or 'cloze')")


def parse_json(path: Path) -> list[_Card]:
    """Parse a JSON deck file into card objects.

    Accepts schema v1 (``mcq`` and ``cloze`` entries) and schema v2, which
    adds ``qa`` cards, MCQs written as ``answer`` + ``distractors``, and the
    shared metadata keys ``id``, ``guid``, ``concept``, ``kind``,
    ``source`` and ``explanation``. Minimal v2 example::

        {
          "schema_version": 2,
          "cards": [
            {"type": "qa", "id": "la.ker.def", "kind": "recall",
             "prompt": "Define ker(T) for T: V -> W.",
             "answer": "{v in V : T(v) = 0}",
             "grading": ["Inputs from V", "T(v) = 0 in W"],
             "explanation": "The kernel lives in the domain.",
             "source": "Johnston, Def. 1.3.1"},
            {"type": "mcq", "id": "la.diag.mcq", "kind": "discriminate",
             "question": "...", "answer": "...",
             "distractors": [{"text": "...", "why_wrong": "..."}],
             "explanation": "..."}
          ],
          "retired": [ ... ]
        }

    ``retired`` (optional) lists notes that a refactor replaced. They are
    still emitted, unchanged except for a ``kunyi::retired`` tag, so that
    importing the deck tags the old notes in the learner's collection and
    they can be deleted with one search (``tag:kunyi::retired``). Each
    retired entry should carry the ``guid`` the note has in Anki.

    "id" is optional but recommended: it keys the note guid (unless an
    explicit "guid" is given), so edits and reordering keep the note's
    review history. Without either, identity falls back to position.

    Parameters
    ----------
    path:
        Path to the JSON file.

    Returns
    -------
    list[BasicCard | MCQCard | ClozeCard]
        Parsed and validated cards, followed by retired notes.

    Raises
    ------
    KeyError
        If a required field is missing from a card entry.
    ValueError
        If a card entry has an unrecognised "type" or fails validation.
    """
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)

    cards: list[_Card] = [_parse_entry(entry) for entry in data["cards"]]
    for entry in data.get("retired", []):
        card = _parse_entry(entry)
        if RETIRED_TAG not in card.tags:
            card.tags.append(RETIRED_TAG)
        cards.append(card)
    return cards


def parse_tsv(path: Path) -> list[BasicCard]:
    """Parse a TSV file into a list of BasicCard objects.

    The file must be tab-delimited UTF-8. An optional header row is detected
    automatically: if the first row contains the literal values "front" and
    "back" (case-insensitive) it is skipped. All other rows are treated as
    card data.

    Expected layout::

        front<TAB>back
        What is spaced repetition?<TAB>A technique that spaces reviews over time.

    Parameters
    ----------
    path:
        Path to the TSV file.

    Returns
    -------
    list[BasicCard]
        Parsed card objects.

    Raises
    ------
    ValueError
        If a row does not have at least two tab-separated columns.
    """
    cards: list[BasicCard] = []

    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        rows = list(reader)

    if not rows:
        return cards

    # Detect and skip header row.
    first = rows[0]
    if len(first) >= 2 and first[0].strip().lower() == "front" and first[1].strip().lower() == "back":
        rows = rows[1:]

    for i, row in enumerate(rows, start=2):  # line numbers for error messages
        if len(row) < 2:
            raise ValueError(
                f"TSV row {i} has fewer than 2 columns: {row!r}"
            )
        cards.append(BasicCard(front=row[0].strip(), back=row[1].strip()))

    return cards
