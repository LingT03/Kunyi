"""Input parsers for kunyi.

Each parser reads a file and returns a list of typed card objects.
Supported formats:
  - JSON: MCQCard list (see README for schema)
  - TSV:  BasicCard list with columns front<TAB>back (optional header row)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from kunyi.card_types import BasicCard, ClozeCard, MCQCard


def parse_json(path: Path) -> list[MCQCard | ClozeCard]:
    """Parse a JSON file into a list of MCQCard/ClozeCard objects.

    Expected schema::

        {
          "cards": [
            {
              "type": "mcq",
              "question": "...",
              "choices": ["A", "B", "C", "D"],
              "correct_answer": "A",
              "explanation": "..."
            },
            {
              "type": "cloze",
              "text": "The mitochondria is the {{c1::powerhouse}} of the cell.",
              "extra": "..."
            }
          ]
        }

    "type" is optional and defaults to "mcq" for backward compatibility
    with files that predate cloze support. The top-level "deck_name" key is
    ignored here; callers pass deck_name separately so the parser stays
    stateless.

    Parameters
    ----------
    path:
        Path to the JSON file.

    Returns
    -------
    list[MCQCard | ClozeCard]
        Parsed and validated card objects.

    Raises
    ------
    KeyError
        If a required field is missing from a card entry.
    ValueError
        If a card entry has an unrecognised "type", or fails validation
        (e.g. correct_answer not in choices, or cloze text has no
        deletion) — propagated from the card dataclasses.
    """
    with path.open(encoding="utf-8") as fh:
        data: dict = json.load(fh)

    cards: list[MCQCard | ClozeCard] = []
    for entry in data["cards"]:
        card_type = entry.get("type", "mcq")
        if card_type == "mcq":
            cards.append(
                MCQCard(
                    question=entry["question"],
                    choices=entry["choices"],
                    correct_answer=entry["correct_answer"],
                    explanation=entry["explanation"],
                    tags=entry.get("tags", []),
                )
            )
        elif card_type == "cloze":
            cards.append(
                ClozeCard(
                    text=entry["text"],
                    extra=entry.get("extra", ""),
                    tags=entry.get("tags", []),
                )
            )
        else:
            raise ValueError(f"Unknown card type {card_type!r} (expected 'mcq' or 'cloze')")
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
