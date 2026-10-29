"""Deck orchestration for kunyi.

AnkiCardDeck wraps a genanki.Deck and handles model selection, note
construction, and media collection. It accepts BasicCard, MCQCard, and
ClozeCard objects via a single add_card() method and dispatches internally.
"""

from __future__ import annotations

import hashlib
import html
import random
from pathlib import Path
from typing import Union

import genanki

from kunyi.card_types import BasicCard, ClozeCard, MCQCard
from kunyi.models import basic_model, cloze_model, mcq_model
from kunyi.presets import apply_preset

Card = Union[BasicCard, MCQCard, ClozeCard]


def _stable_id(seed: str) -> int:
    """Derive a deterministic, Anki-sized integer ID from a string seed.

    Anki deck/model ids just need to be large, roughly-unique positive
    integers; genanki's own examples use 10-digit values in the same range
    as a Unix timestamp. Hashing the seed (instead of using time.time())
    means re-running kunyi against an unchanged deck name always yields the
    same deck_id, which is what lets Anki recognise a re-import as an
    update to an existing deck rather than a brand-new one.

    Parameters
    ----------
    seed:
        Any string whose identity should map to a stable integer (e.g. a
        deck name).

    Returns
    -------
    int
        A positive integer in [0, 10_000_000_000), stable for a given seed.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % 10_000_000_000


class AnkiCardDeck:
    """Build an Anki deck from BasicCard, MCQCard, and ClozeCard objects.

    A single deck can hold mixed card types. Each type uses its own genanki
    Model (keyed by a deterministic model_id derived from deck_id). Notes are
    added in insertion order.

    Each note is also given a deterministic, content-independent guid (see
    `_note_guid`), so editing a card's text in a later regeneration no
    longer changes that note's identity: Anki matches it to the existing
    note and updates it in place instead of creating a duplicate. Deck and
    model ids are likewise deterministic by default (see `_stable_id`), so
    re-running kunyi with the same deck_name updates the same Anki deck
    instead of importing a second copy of it.

    Media file paths collected from card.media_paths are passed to
    genanki.Package at save time. The actual media wiring is reserved — files
    are registered but card templates do not yet reference them by name.

    Parameters
    ----------
    deck_name:
        Human-readable name shown in Anki.
    deck_id:
        Optional stable integer deck ID. Defaults to a deterministic value
        derived from deck_name (see `_stable_id`), so regenerating the same
        deck name always reuses the same deck_id and Anki updates the
        existing deck instead of creating a duplicate. Pass an explicit
        value only if you need to decouple deck identity from deck_name
        (e.g. two decks that happen to share a display name).
    """

    def __init__(
        self,
        deck_name: str,
        deck_id: int | None = None,
    ) -> None:
        self.deck_name = deck_name
        self.deck_id: int = deck_id if deck_id is not None else _stable_id(deck_name)

        # Derive stable model IDs from deck_id so they travel together.
        self._basic_model: genanki.Model = basic_model(self.deck_id + 1)
        self._mcq_model: genanki.Model = mcq_model(self.deck_id + 2)
        self._cloze_model: genanki.Model = cloze_model(self.deck_id + 3)

        self._deck: genanki.Deck = genanki.Deck(self.deck_id, self.deck_name)
        self._media_files: list[str] = []

        # Position of the next card within this deck, used as a fallback
        # note-identity seed for cards with no explicit note_id. See
        # `_note_guid`.
        self._next_index: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def add_card(self, card: Card) -> None:
        """Add a single card to the deck.

        Dispatches to the correct note builder based on card type.

        Parameters
        ----------
        card:
            A BasicCard, MCQCard, or ClozeCard instance.

        Raises
        ------
        TypeError
            If card is not a recognised card type.
        """
        if isinstance(card, BasicCard):
            guid = self._note_guid(card, "basic")
            note = self._build_basic_note(card, guid)
        elif isinstance(card, MCQCard):
            guid = self._note_guid(card, "mcq")
            note = self._build_mcq_note(card, guid)
        elif isinstance(card, ClozeCard):
            guid = self._note_guid(card, "cloze")
            note = self._build_cloze_note(card, guid)
        else:
            raise TypeError(f"Unsupported card type: {type(card)!r}")

        self._deck.add_note(note)
        self._next_index += 1

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
    # Private helpers
    # ------------------------------------------------------------------

    def _note_guid(self, card: Card, card_type: str) -> str:
        """Compute a stable, content-independent guid for a note.

        genanki's default guid (used when Note(guid=...) is omitted) is a
        hash of the note's own field content, so editing even one word of a
        card changes its guid and Anki treats the next import as a brand
        new note rather than an update to the existing one. This method
        gives every note an identity that survives content edits:

        - If the card was parsed with an explicit ``id`` (``card.note_id``),
          the guid is derived from (deck_name, card_type, note_id). This
          stays stable even if cards are reordered or other cards are
          inserted/removed elsewhere in the file — the recommended path for
          any card you intend to keep editing over time.
        - If the card carries an explicit ``guid`` it is used verbatim. This
          pins a note to an identity it already has in a user's collection
          (e.g. a migrated note originally identified by position), so its
          review history survives renaming ``note_id`` or reordering.
        - Otherwise, the guid falls back to (deck_name, card_type,
          position-in-deck). This is stable across regenerations as long as
          the card's position doesn't change; inserting or removing an
          earlier card of any type will shift later cards' identities.

        Parameters
        ----------
        card:
            The card being added.
        card_type:
            One of "basic", "mcq", "cloze" — included so that, in the
            position-fallback case, a BasicCard and an MCQCard that happen
            to land at the same index don't collide.

        Returns
        -------
        str
            A guid suitable for genanki.Note(guid=...).
        """
        explicit_guid = getattr(card, "guid", None)
        if explicit_guid:
            return explicit_guid
        note_id = getattr(card, "note_id", None)
        seed = note_id if note_id else f"index:{self._next_index}"
        return genanki.guid_for(self.deck_name, card_type, seed)

    def _build_basic_note(self, card: BasicCard, guid: str) -> genanki.Note:
        """Construct a genanki Note from a BasicCard.

        Parameters
        ----------
        card:
            Source BasicCard.
        guid:
            Stable note identity; see `_note_guid`.

        Returns
        -------
        genanki.Note
            Note bound to the basic model with Front and Back fields.
        """
        return genanki.Note(
            model=self._basic_model,
            fields=[card.front, self._format_basic_back(card)],
            tags=self._note_tags(card),
            guid=guid,
        )

    def _build_mcq_note(self, card: MCQCard, guid: str) -> genanki.Note:
        """Construct a genanki Note from an MCQCard.

        The Question field contains the formatted question stem and numbered
        choices separated by HTML line breaks. The Answer field contains the
        correct answer and explanation in bold-labelled HTML.

        Parameters
        ----------
        card:
            Source MCQCard.
        guid:
            Stable note identity; see `_note_guid`.

        Returns
        -------
        genanki.Note
            Note bound to the MCQ model.
        """
        return genanki.Note(
            model=self._mcq_model,
            fields=[
                self._format_mcq_front(card, seed=guid),
                self._format_mcq_back(card, seed=guid),
            ],
            tags=self._note_tags(card),
            guid=guid,
        )

    def _build_cloze_note(self, card: ClozeCard, guid: str) -> genanki.Note:
        """Construct a genanki Note from a ClozeCard.

        Parameters
        ----------
        card:
            Source ClozeCard.
        guid:
            Stable note identity; see `_note_guid`.

        Returns
        -------
        genanki.Note
            Note bound to the cloze model with Text and Extra fields.
        """
        return genanki.Note(
            model=self._cloze_model,
            fields=[card.text, self._format_cloze_extra(card)],
            tags=self._note_tags(card),
            guid=guid,
        )

    @staticmethod
    def _note_tags(card: Card) -> list[str]:
        """Return the card's tags plus a ``kind::<kind>`` tag when kind is set.

        Parameters
        ----------
        card:
            Source card.

        Returns
        -------
        list[str]
            Tags in authoring order, de-duplicated, with the kind tag last.
        """
        tags = list(dict.fromkeys(card.tags))
        if card.kind and f"kind::{card.kind}" not in tags:
            tags.append(f"kind::{card.kind}")
        return tags

    @staticmethod
    def _render_feedback(explanation: str, source: str) -> str:
        """Render the shared explanation + source footer used on card backs.

        Parameters
        ----------
        explanation:
            Feedback text (may contain HTML/MathJax). Empty to omit.
        source:
            Citation. Rendered small and muted, as metadata. Empty to omit.

        Returns
        -------
        str
            HTML fragment (possibly empty).
        """
        parts: list[str] = []
        if explanation.strip():
            parts.append(f"<div class='kunyi-explanation'><strong>Why:</strong> {explanation}</div>")
        if source.strip():
            parts.append(
                "<div class='kunyi-source' style='font-size:0.8em;opacity:0.7;margin-top:0.6em'>"
                f"Source: {html.escape(source.strip('[] '))}</div>"
            )
        return "".join(parts)

    @classmethod
    def _format_basic_back(cls, card: BasicCard) -> str:
        """Render the back HTML for a BasicCard.

        The graded answer comes first, then the pass criteria, then feedback,
        so the learner grades against the answer rather than the explanation.

        Parameters
        ----------
        card:
            Source BasicCard.

        Returns
        -------
        str
            HTML string. Equal to ``card.back`` when the card has no
            grading, explanation or source (v1 behaviour).
        """
        if not (card.grading or card.explanation.strip() or card.source.strip()):
            return card.back
        out = f"<div class='kunyi-answer'>{card.back}</div>"
        if card.grading:
            items = "".join(f"<li>{g}</li>" for g in card.grading)
            out += (
                "<div class='kunyi-grading' style='font-size:0.9em;margin-top:0.6em'>"
                f"<strong>Counts as correct if:</strong><ul>{items}</ul></div>"
            )
        return out + cls._render_feedback(card.explanation, card.source)

    @classmethod
    def _format_cloze_extra(cls, card: ClozeCard) -> str:
        """Render the Extra field for a ClozeCard.

        Parameters
        ----------
        card:
            Source ClozeCard.

        Returns
        -------
        str
            ``card.extra`` unchanged for v1 cards (no explanation/source);
            otherwise the rendered explanation + source footer.
        """
        if not card.explanation.strip() and not card.source.strip():
            return card.extra
        explanation = card.explanation or card.extra
        return cls._render_feedback(explanation, card.source)

    @staticmethod
    def _choice_order(card: MCQCard, seed: str | None) -> list[int]:
        """Return a deterministic permutation of choice indices.

        LLM-written MCQs almost always list the key first; rendering in file
        order lets position alone answer the card. The order is seeded by the
        note guid, so a rebuild never reshuffles a note the learner has seen,
        while different notes get independent orders.

        Parameters
        ----------
        card:
            Source MCQCard.
        seed:
            Usually the note guid. Falls back to note_id, then the question.

        Returns
        -------
        list[int]
            Indices into ``card.choices`` in display order.
        """
        key = seed or card.note_id or card.question
        order = list(range(len(card.choices)))
        random.Random(f"kunyi-mcq:{key}").shuffle(order)
        return order

    @classmethod
    def _format_mcq_front(cls, card: MCQCard, seed: str | None = None) -> str:
        """Render the front HTML for an MCQCard.

        Parameters
        ----------
        card:
            Source MCQCard.
        seed:
            Shuffle seed (the note guid when called from add_card).

        Returns
        -------
        str
            HTML string: question stem followed by lettered, shuffled choices.
        """
        order = cls._choice_order(card, seed)
        lines = "<br>".join(
            f"{chr(ord('A') + pos)}. {card.choices[i]}" for pos, i in enumerate(order)
        )
        return f"{card.question}<br><br>{lines}"

    @classmethod
    def _format_mcq_back(cls, card: MCQCard, seed: str | None = None) -> str:
        """Render the back HTML for an MCQCard.

        Parameters
        ----------
        card:
            Source MCQCard.
        seed:
            Shuffle seed; must match the one used for the front.

        Returns
        -------
        str
            HTML: correct letter and text, explanation, why each distractor
            is wrong (when given), a grading reminder, and the source.
        """
        order = cls._choice_order(card, seed)
        key_pos = order.index(card.choices.index(card.correct_answer))
        letter = chr(ord("A") + key_pos)
        out = f"<strong>Correct answer:</strong> {letter}. {card.correct_answer}<br><br>"
        out += f"<strong>Explanation:</strong> {card.explanation}"
        if card.distractor_notes:
            rows = "".join(
                f"<li>{chr(ord('A') + pos)}. {card.distractor_notes[card.choices[i]]}</li>"
                for pos, i in enumerate(order)
                if card.choices[i] in card.distractor_notes
            )
            out += f"<div style='margin-top:0.6em'><strong>Why the others are wrong:</strong><ul>{rows}</ul></div>"
        out += (
            "<div style='font-size:0.85em;opacity:0.75;margin-top:0.6em'>"
            "If you guessed or got there only by elimination, press Again.</div>"
        )
        if card.source.strip():
            out += cls._render_feedback("", card.source)
        return out
