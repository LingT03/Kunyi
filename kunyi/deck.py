"""Deck orchestration for kunyi.

AnkiCardDeck wraps a genanki.Deck and handles model selection, note
construction, and media collection. It accepts both BasicCard and MCQCard
objects via a single add_card() method and dispatches internally.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Union

import genanki

from kunyi.card_types import BasicCard, ClozeCard, MCQCard
from kunyi.models import basic_model, cloze_model, mcq_model
from kunyi.presets import apply_preset

Card = Union[BasicCard, MCQCard, ClozeCard]


class AnkiCardDeck:
    """Build an Anki deck from BasicCard and MCQCard objects.

    A single deck can hold mixed card types. Each type uses its own genanki
    Model (keyed by a deterministic model_id derived from deck_id). Notes are
    added in insertion order.

    Media file paths collected from card.media_paths are passed to
    genanki.Package at save time. The actual media wiring is reserved — files
    are registered but card templates do not yet reference them by name.

    Parameters
    ----------
    deck_name:
        Human-readable name shown in Anki.
    deck_id:
        Optional stable integer deck ID. Defaults to a time-based value.
        Use a fixed value when regenerating the same deck so Anki updates
        existing notes instead of creating duplicates.
    """

    def __init__(
        self,
        deck_name: str,
        deck_id: int | None = None,
    ) -> None:
        self.deck_name = deck_name
        self.deck_id: int = deck_id if deck_id is not None else int(time.time())

        # Derive stable model IDs from deck_id so they travel together.
        self._basic_model: genanki.Model = basic_model(self.deck_id + 1)
        self._mcq_model: genanki.Model = mcq_model(self.deck_id + 2)
        self._cloze_model: genanki.Model = cloze_model(self.deck_id + 3)

        self._deck: genanki.Deck = genanki.Deck(self.deck_id, self.deck_name)
        self._media_files: list[str] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def add_card(self, card: Card) -> None:
        """Add a single card to the deck.

        Dispatches to the correct note builder based on card type.

        Parameters
        ----------
        card:
            A BasicCard or MCQCard instance.

        Raises
        ------
        TypeError
            If card is not a recognised card type.
        """
        if isinstance(card, BasicCard):
            note = self._build_basic_note(card)
        elif isinstance(card, MCQCard):
            note = self._build_mcq_note(card)
        elif isinstance(card, ClozeCard):
            note = self._build_cloze_note(card)
        else:
            raise TypeError(f"Unsupported card type: {type(card)!r}")

        self._deck.add_note(note)

        # Accumulate media for registration at save time.
        for media_path in card.media_paths:
            self._media_files.append(str(media_path))

    def save_deck(self, output_path: Path, preset: str | None = None) -> None:
        """Write the deck to an .apkg file.

        Parameters
        ----------
        output_path:
            Destination path for the .apkg file. Parent directories must
            already exist; callers are responsible for creation.
        preset:
            Optional deck-options preset name (see kunyi.presets.PRESETS)
            applied after writing, e.g. "exam_sprint" raises Anki's daily
            new/review card limits so a freshly generated deck isn't
            throttled by the default 20-new-cards-per-day cap. None (the
            default) leaves Anki's defaults untouched.
        """
        package = genanki.Package(self._deck)
        # Media wiring: reserved for future use. Files are registered here
        # so the field exists in the package; card templates will reference
        # them by filename once image/audio card types are added.
        if self._media_files:
            package.media_files = self._media_files

        package.write_to_file(str(output_path))

        if preset is not None:
            apply_preset(output_path, preset)

    # ------------------------------------------------------------------
    # Private note builders
    # ------------------------------------------------------------------

    def _build_basic_note(self, card: BasicCard) -> genanki.Note:
        """Construct a genanki Note from a BasicCard.

        Parameters
        ----------
        card:
            Source BasicCard.

        Returns
        -------
        genanki.Note
            Note bound to the basic model with Front and Back fields.
        """
        return genanki.Note(
            model=self._basic_model,
            fields=[card.front, card.back],
            tags=card.tags,
        )

    def _build_mcq_note(self, card: MCQCard) -> genanki.Note:
        """Construct a genanki Note from an MCQCard.

        The Question field contains the formatted question stem and numbered
        choices separated by HTML line breaks. The Answer field contains the
        correct answer and explanation in bold-labelled HTML.

        Parameters
        ----------
        card:
            Source MCQCard.

        Returns
        -------
        genanki.Note
            Note bound to the MCQ model.
        """
        return genanki.Note(
            model=self._mcq_model,
            fields=[
                self._format_mcq_front(card),
                self._format_mcq_back(card),
            ],
            tags=card.tags,
        )

    def _build_cloze_note(self, card: ClozeCard) -> genanki.Note:
        """Construct a genanki Note from a ClozeCard.

        Parameters
        ----------
        card:
            Source ClozeCard.

        Returns
        -------
        genanki.Note
            Note bound to the cloze model with Text and Extra fields.
        """
        return genanki.Note(
            model=self._cloze_model,
            fields=[card.text, card.extra],
            tags=card.tags,
        )

    @staticmethod
    def _format_mcq_front(card: MCQCard) -> str:
        """Render the front HTML for an MCQCard.

        Parameters
        ----------
        card:
            Source MCQCard.

        Returns
        -------
        str
            HTML string: question stem followed by numbered choices.
        """
        numbered = "<br>".join(f"{i}. {c}" for i, c in enumerate(card.choices, 1))
        return f"{card.question}<br><br>{numbered}"

    @staticmethod
    def _format_mcq_back(card: MCQCard) -> str:
        """Render the back HTML for an MCQCard.

        Parameters
        ----------
        card:
            Source MCQCard.

        Returns
        -------
        str
            HTML string: bolded correct answer label followed by explanation.
        """
        answer_line = f"<strong>Correct answer:</strong> {card.correct_answer}<br><br>"
        explanation_line = f"<strong>Explanation:</strong> {card.explanation}"
        return answer_line + explanation_line
