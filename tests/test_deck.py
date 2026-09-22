"""Unit tests for kunyi.

Tests cover:
  - MCQCard construction and correct_answer validation
  - BasicCard construction
  - HTML formatting for MCQ front and back
  - AnkiCardDeck.save_deck() writes a valid .apkg to a temp directory
  - parse_tsv() with and without a header row
  - parse_json() round-trip
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest

from kunyi.card_types import BasicCard, ClozeCard, MCQCard
from kunyi.deck import AnkiCardDeck
from kunyi.parsers import parse_json, parse_tsv
from kunyi.presets import PRESETS, apply_preset


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_mcq() -> MCQCard:
    return MCQCard(
        question="What does CPU stand for?",
        choices=["Central Processing Unit", "Core Power Unit", "Control Processing Unit"],
        correct_answer="Central Processing Unit",
        explanation="CPU stands for Central Processing Unit.",
    )


@pytest.fixture
def valid_basic() -> BasicCard:
    return BasicCard(front="What is spaced repetition?", back="A technique that spaces reviews.")


@pytest.fixture
def valid_cloze() -> ClozeCard:
    return ClozeCard(
        text="The mitochondria is the {{c1::powerhouse}} of the cell.",
        extra="Mitochondria generate ATP via oxidative phosphorylation.",
    )


# ---------------------------------------------------------------------------
# MCQCard
# ---------------------------------------------------------------------------


class TestMCQCard:
    def test_valid_construction(self, valid_mcq: MCQCard) -> None:
        assert valid_mcq.question == "What does CPU stand for?"
        assert len(valid_mcq.choices) == 3
        assert valid_mcq.correct_answer == "Central Processing Unit"
        assert valid_mcq.tags == []
        assert valid_mcq.media_paths == []

    def test_correct_answer_not_in_choices_raises(self) -> None:
        with pytest.raises(ValueError, match="not in choices"):
            MCQCard(
                question="Q?",
                choices=["A", "B"],
                correct_answer="C",
                explanation="...",
            )

    def test_tags_reserved_field(self) -> None:
        card = MCQCard(
            question="Q?",
            choices=["A"],
            correct_answer="A",
            explanation=".",
            tags=["exam", "chapter-1"],
        )
        assert card.tags == ["exam", "chapter-1"]

    def test_empty_question_raises(self) -> None:
        with pytest.raises(ValueError, match="question cannot be empty"):
            MCQCard(question="   ", choices=["A"], correct_answer="A", explanation=".")

    def test_empty_choices_raises(self) -> None:
        with pytest.raises(ValueError, match="choices cannot be empty"):
            MCQCard(question="Q?", choices=[], correct_answer="A", explanation=".")


# ---------------------------------------------------------------------------
# BasicCard
# ---------------------------------------------------------------------------


class TestBasicCard:
    def test_valid_construction(self, valid_basic: BasicCard) -> None:
        assert valid_basic.front == "What is spaced repetition?"
        assert valid_basic.back == "A technique that spaces reviews."
        assert valid_basic.tags == []
        assert valid_basic.media_paths == []

    def test_empty_front_raises(self) -> None:
        with pytest.raises(ValueError, match="front cannot be empty"):
            BasicCard(front="  ", back="Answer")

    def test_empty_back_raises(self) -> None:
        with pytest.raises(ValueError, match="back cannot be empty"):
            BasicCard(front="Question", back="")


# ---------------------------------------------------------------------------
# ClozeCard
# ---------------------------------------------------------------------------


class TestClozeCard:
    def test_valid_construction(self, valid_cloze: ClozeCard) -> None:
        assert "{{c1::powerhouse}}" in valid_cloze.text
        assert valid_cloze.extra
        assert valid_cloze.tags == []

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ValueError, match="text cannot be empty"):
            ClozeCard(text="   ")

    def test_missing_cloze_deletion_raises(self) -> None:
        with pytest.raises(ValueError, match="no cloze deletion"):
            ClozeCard(text="This has no deletion markers at all.")

    def test_multiple_deletions_allowed(self) -> None:
        card = ClozeCard(text="{{c1::A}} and {{c2::B}} are both deletions.")
        assert card.text


# ---------------------------------------------------------------------------
# MCQ HTML formatting
# ---------------------------------------------------------------------------


class TestMCQFormatting:
    def test_format_front_contains_question(self, valid_mcq: MCQCard) -> None:
        result = AnkiCardDeck._format_mcq_front(valid_mcq)
        assert valid_mcq.question in result

    def test_format_front_numbers_choices(self, valid_mcq: MCQCard) -> None:
        result = AnkiCardDeck._format_mcq_front(valid_mcq)
        assert "1. Central Processing Unit" in result
        assert "2. Core Power Unit" in result
        assert "3. Control Processing Unit" in result

    def test_format_back_contains_answer_and_explanation(self, valid_mcq: MCQCard) -> None:
        result = AnkiCardDeck._format_mcq_back(valid_mcq)
        assert valid_mcq.correct_answer in result
        assert valid_mcq.explanation in result

    def test_format_back_uses_sentence_case_labels(self, valid_mcq: MCQCard) -> None:
        result = AnkiCardDeck._format_mcq_back(valid_mcq)
        assert "Correct answer:" in result
        assert "Explanation:" in result


# ---------------------------------------------------------------------------
# AnkiCardDeck.save_deck
# ---------------------------------------------------------------------------


class TestAnkiCardDeck:
    def test_save_deck_creates_apkg(
        self, valid_mcq: MCQCard, valid_basic: BasicCard, valid_cloze: ClozeCard
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.apkg"
            deck = AnkiCardDeck(deck_name="Test Deck", deck_id=99999001)
            deck.add_card(valid_mcq)
            deck.add_card(valid_basic)
            deck.add_card(valid_cloze)
            deck.save_deck(output)

            assert output.exists()
            # .apkg files are ZIP archives; verify the file is valid.
            assert zipfile.is_zipfile(output)

    def test_add_card_unsupported_type_raises(self) -> None:
        deck = AnkiCardDeck(deck_name="Test", deck_id=99999002)
        with pytest.raises(TypeError, match="Unsupported card type"):
            deck.add_card("not a card")  # type: ignore[arg-type]

    def test_save_deck_with_preset_applies_limits(self, valid_cloze: ClozeCard) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.apkg"
            deck = AnkiCardDeck(deck_name="Cram Deck", deck_id=99999003)
            deck.add_card(valid_cloze)
            deck.save_deck(output, preset="exam_sprint")

            with tempfile.TemporaryDirectory() as extract_dir:
                with zipfile.ZipFile(output) as zf:
                    zf.extractall(extract_dir)
                conn = sqlite3.connect(Path(extract_dir) / "collection.anki2")
                (dconf_json,) = conn.execute("SELECT dconf FROM col").fetchone()
                conn.close()
                dconf = json.loads(dconf_json)
                for group in dconf.values():
                    assert group["new"]["perDay"] == 9999
                    assert group["rev"]["perDay"] == 9999


# ---------------------------------------------------------------------------
# TSV parser
# ---------------------------------------------------------------------------


class TestParseTSV:
    def _write_tsv(self, content: str, tmp_path: Path) -> Path:
        p = tmp_path / "cards.tsv"
        p.write_text(content, encoding="utf-8")
        return p

    def test_parses_cards_without_header(self, tmp_path: Path) -> None:
        p = self._write_tsv("What is RAM?\tRandom Access Memory\n", tmp_path)
        cards = parse_tsv(p)
        assert len(cards) == 1
        assert cards[0].front == "What is RAM?"
        assert cards[0].back == "Random Access Memory"

    def test_skips_header_row(self, tmp_path: Path) -> None:
        p = self._write_tsv("front\tback\nWhat is RAM?\tRandom Access Memory\n", tmp_path)
        cards = parse_tsv(p)
        assert len(cards) == 1

    def test_case_insensitive_header_detection(self, tmp_path: Path) -> None:
        p = self._write_tsv("Front\tBack\nQ\tA\n", tmp_path)
        cards = parse_tsv(p)
        assert len(cards) == 1

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        p = self._write_tsv("", tmp_path)
        assert parse_tsv(p) == []

    def test_row_with_fewer_than_two_columns_raises(self, tmp_path: Path) -> None:
        p = self._write_tsv("front\tback\nonly_one_column\n", tmp_path)
        with pytest.raises(ValueError, match="fewer than 2 columns"):
            parse_tsv(p)


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------


class TestParseJSON:
    def test_round_trip(self, tmp_path: Path) -> None:
        data = {
            "cards": [
                {
                    "question": "Q1?",
                    "choices": ["A", "B", "C"],
                    "correct_answer": "A",
                    "explanation": "Because A.",
                    "tags": ["tag1"],
                }
            ]
        }
        p = tmp_path / "cards.json"
        p.write_text(json.dumps(data), encoding="utf-8")

        cards = parse_json(p)
        assert len(cards) == 1
        assert cards[0].question == "Q1?"
        assert cards[0].correct_answer == "A"
        assert cards[0].tags == ["tag1"]

    def test_missing_key_raises(self, tmp_path: Path) -> None:
        data = {"cards": [{"question": "Q?"}]}  # missing required fields
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(KeyError):
            parse_json(p)

    def test_defaults_to_mcq_without_type(self, tmp_path: Path) -> None:
        data = {
            "cards": [
                {
                    "question": "Q1?",
                    "choices": ["A", "B"],
                    "correct_answer": "A",
                    "explanation": "Because A.",
                }
            ]
        }
        p = tmp_path / "cards.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        cards = parse_json(p)
        assert isinstance(cards[0], MCQCard)

    def test_parses_cloze_cards(self, tmp_path: Path) -> None:
        data = {
            "cards": [
                {
                    "type": "cloze",
                    "text": "The powerhouse of the cell is the {{c1::mitochondria}}.",
                    "extra": "Generates ATP.",
                    "tags": ["biology"],
                }
            ]
        }
        p = tmp_path / "cloze.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        cards = parse_json(p)
        assert len(cards) == 1
        assert isinstance(cards[0], ClozeCard)
        assert "{{c1::mitochondria}}" in cards[0].text
        assert cards[0].extra == "Generates ATP."
        assert cards[0].tags == ["biology"]

    def test_mixed_types_in_one_file(self, tmp_path: Path) -> None:
        data = {
            "cards": [
                {
                    "type": "mcq",
                    "question": "Q?",
                    "choices": ["A", "B"],
                    "correct_answer": "A",
                    "explanation": ".",
                },
                {"type": "cloze", "text": "{{c1::Answer}} here."},
            ]
        }
        p = tmp_path / "mixed.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        cards = parse_json(p)
        assert isinstance(cards[0], MCQCard)
        assert isinstance(cards[1], ClozeCard)

    def test_unknown_type_raises(self, tmp_path: Path) -> None:
        data = {"cards": [{"type": "flashcard-supreme", "text": "..."}]}
        p = tmp_path / "bad_type.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="Unknown card type"):
            parse_json(p)


# ---------------------------------------------------------------------------
# Deck-option presets
# ---------------------------------------------------------------------------


class TestPresets:
    def test_unknown_preset_raises(self, valid_cloze: ClozeCard) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.apkg"
            deck = AnkiCardDeck(deck_name="Test", deck_id=99999004)
            deck.add_card(valid_cloze)
            deck.save_deck(output)
            with pytest.raises(KeyError, match="Unknown preset"):
                apply_preset(output, "not_a_real_preset")

    def test_exam_sprint_is_a_known_preset(self) -> None:
        assert "exam_sprint" in PRESETS
