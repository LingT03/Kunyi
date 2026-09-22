"""CLI entry point for kunyi.

Usage
-----
    kunyi <deck_name> <input_file> [--format {json,tsv}] [--output PATH]

On success:  the resolved output path is printed to stdout, exit 0.
On failure:  a human-readable message is printed to stderr, exit 1.

This contract is designed for subprocess callers (e.g. Seya/Dart):
  - check the exit code
  - on 0: read stdout as the .apkg path
  - on non-zero: read stderr for the error message
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kunyi.card_types import BasicCard, ClozeCard, MCQCard
from kunyi.deck import AnkiCardDeck
from kunyi.parsers import parse_json, parse_tsv
from kunyi.presets import PRESETS


def _detect_format(path: Path) -> str:
    """Infer input format from file extension.

    Parameters
    ----------
    path:
        Input file path.

    Returns
    -------
    str
        "json" or "tsv".

    Raises
    ------
    ValueError
        If the extension is not recognised.
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".tsv", ".txt"}:
        return "tsv"
    raise ValueError(
        f"Cannot infer format from extension {suffix!r}. "
        "Use --format {{json,tsv}} to specify explicitly."
    )


def _resolve_output(deck_name: str, output: str | None) -> Path:
    """Resolve the .apkg output path.

    Parameters
    ----------
    deck_name:
        Deck name used to build the default filename.
    output:
        Explicit output path from --output, or None for the default.

    Returns
    -------
    Path
        Resolved output path.
    """
    if output is not None:
        return Path(output)
    safe_name = deck_name.replace(" ", "_")
    return Path(f"{safe_name}.apkg")


def main() -> None:
    """Parse arguments and generate the .apkg deck."""
    parser = argparse.ArgumentParser(
        prog="kunyi",
        description="Generate an Anki .apkg deck from JSON or TSV card data.",
    )
    parser.add_argument("deck_name", help="Name of the Anki deck.")
    parser.add_argument("input_file", help="Path to the input file (.json or .tsv).")
    parser.add_argument(
        "--format",
        choices=["json", "tsv"],
        default=None,
        help="Input format. Defaults to auto-detection by file extension.",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help=(
            "Output path for the .apkg file. "
            "Defaults to <deck_name>.apkg in the current directory."
        ),
    )
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default=None,
        help=(
            "Deck-options preset applied after building. 'exam_sprint' raises "
            "Anki's daily new/review card limits so a freshly generated deck "
            "isn't throttled by the default 20-new-cards-per-day cap."
        ),
    )

    args = parser.parse_args()
    input_path = Path(args.input_file)

    # Resolve format: explicit flag wins over extension detection.
    try:
        fmt = args.format or _detect_format(input_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Parse input file.
    try:
        if fmt == "json":
            cards: list[BasicCard | MCQCard | ClozeCard] = parse_json(input_path)
        else:
            cards = parse_tsv(input_path)
    except FileNotFoundError:
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, ValueError) as exc:
        print(f"error: failed to parse {input_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Build and save deck.
    output_path = _resolve_output(args.deck_name, args.output)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        deck = AnkiCardDeck(deck_name=args.deck_name)
        for card in cards:
            deck.add_card(card)
        deck.save_deck(output_path, preset=args.preset)
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to write deck: {exc}", file=sys.stderr)
        sys.exit(1)

    # Print resolved path to stdout for subprocess callers.
    print(output_path.resolve())


if __name__ == "__main__":
    main()
