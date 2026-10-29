"""Unit tests for kunyi.

Tests cover:
  - MCQCard construction and correct_answer validation
  - BasicCard construction
  - HTML formatting for MCQ front and back
  - AnkiCardDeck.save_deck() writes a valid .apkg to a temp directory
  - parse_tsv() with and without a header row
  - parse_json() round-trip
  - Stable deck ids and content-independent note guids across regenerations
  - AnkiConnect client request shape and error handling
"""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import urllib.error
import zipfile
from pathlib import Path

import pytest

from kunyi import ankiconnect
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

    def test_format_front_letters_every_choice_once(self, valid_mcq: MCQCard) -> None:
        result = AnkiCardDeck._format_mcq_front(valid_mcq, seed="g1")
        for choice in valid_mcq.choices:
            assert result.count(f". {choice}") == 1
        for letter in "ABC":
            assert f"{letter}. " in result

    def test_shuffle_is_deterministic_per_seed(self, valid_mcq: MCQCard) -> None:
        a = AnkiCardDeck._format_mcq_front(valid_mcq, seed="same")
        b = AnkiCardDeck._format_mcq_front(valid_mcq, seed="same")
        assert a == b

    def test_key_position_is_not_fixed(self) -> None:
        # Key authored first in every card; rendered positions must vary.
        positions = set()
        for i in range(40):
            card = MCQCard(question=f"Q{i}?", choices=["key", "d1", "d2", "d3"],
                           correct_answer="key", explanation="because.")
            order = AnkiCardDeck._choice_order(card, f"guid-{i}")
            positions.add(order.index(0))
        assert positions == {0, 1, 2, 3}

    def test_back_letter_matches_front(self, valid_mcq: MCQCard) -> None:
        front = AnkiCardDeck._format_mcq_front(valid_mcq, seed="x")
        back = AnkiCardDeck._format_mcq_back(valid_mcq, seed="x")
        letter = back.split("</strong> ")[1][0]
        assert f"{letter}. {valid_mcq.correct_answer}" in front

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

    def test_optional_id_becomes_note_id(self, tmp_path: Path) -> None:
        data = {
            "cards": [
                {
                    "type": "mcq",
                    "id": "basics-001",
                    "question": "Q?",
                    "choices": ["A", "B"],
                    "correct_answer": "A",
                    "explanation": ".",
                },
                {"type": "cloze", "id": "basics-002", "text": "{{c1::Answer}} here."},
                {"type": "cloze", "text": "{{c1::No id}} here."},
            ]
        }
        p = tmp_path / "ids.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        cards = parse_json(p)
        assert [c.note_id for c in cards] == ["basics-001", "basics-002", None]


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


# ---------------------------------------------------------------------------
# Stable identity across regenerations
# ---------------------------------------------------------------------------


def _guids(deck: AnkiCardDeck) -> list[str]:
    return [note.guid for note in deck._deck.notes]


def _build(name: str, cards: list) -> AnkiCardDeck:
    deck = AnkiCardDeck(deck_name=name)
    for card in cards:
        deck.add_card(card)
    return deck


class TestStableIdentity:
    def test_default_deck_id_is_deterministic(self) -> None:
        assert AnkiCardDeck("Probability Basics").deck_id == AnkiCardDeck("Probability Basics").deck_id

    def test_default_deck_id_differs_by_name(self) -> None:
        assert AnkiCardDeck("Probability Basics").deck_id != AnkiCardDeck("Stats & ML Basics").deck_id

    def test_explicit_deck_id_overrides_default(self) -> None:
        assert AnkiCardDeck("Probability Basics", deck_id=99999005).deck_id == 99999005

    def test_guid_survives_content_edit_by_position(self) -> None:
        before = _build("D", [ClozeCard(text="{{c1::Old}} wording.")])
        after = _build("D", [ClozeCard(text="{{c1::New}} wording, fixed typo.")])
        assert _guids(before) == _guids(after)

    def test_guid_survives_content_edit_and_reorder_with_note_id(self) -> None:
        before = _build(
            "D",
            [
                ClozeCard(text="{{c1::First}}.", note_id="a"),
                ClozeCard(text="{{c1::Second}}.", note_id="b"),
            ],
        )
        after = _build(
            "D",
            [
                ClozeCard(text="{{c1::Second}}, edited.", note_id="b"),
                ClozeCard(text="{{c1::Inserted}}.", note_id="c"),
                ClozeCard(text="{{c1::First}}, edited.", note_id="a"),
            ],
        )
        a, b = _guids(before)
        assert _guids(after)[2] == a
        assert _guids(after)[0] == b

    def test_guids_are_unique_within_a_deck(self) -> None:
        deck = _build("D", [ClozeCard(text="{{c1::Same}} text.") for _ in range(5)])
        assert len(set(_guids(deck))) == 5

    def test_card_type_disambiguates_same_position(self) -> None:
        basic = _build("D", [BasicCard(front="Q", back="A")])
        cloze = _build("D", [ClozeCard(text="{{c1::A}}.")])
        assert _guids(basic) != _guids(cloze)

    def test_guids_differ_between_decks(self) -> None:
        one = _build("Deck One", [ClozeCard(text="{{c1::A}}.", note_id="x")])
        two = _build("Deck Two", [ClozeCard(text="{{c1::A}}.", note_id="x")])
        assert _guids(one) != _guids(two)

    def test_regenerated_apkg_keeps_ids_when_one_card_is_edited(
        self, tmp_path: Path, valid_mcq: MCQCard, valid_cloze: ClozeCard
    ) -> None:
        def build_and_read(name: str, cloze_text: str) -> tuple[list[str], list[int], list[int]]:
            output = tmp_path / name
            deck = _build("Regen Deck", [valid_mcq, ClozeCard(text=cloze_text), valid_cloze])
            deck.save_deck(output)
            with zipfile.ZipFile(output) as zf:
                zf.extract("collection.anki2", tmp_path / name.replace(".apkg", ""))
            conn = sqlite3.connect(tmp_path / name.replace(".apkg", "") / "collection.anki2")
            guids = [g for (g,) in conn.execute("SELECT guid FROM notes ORDER BY id")]
            mids = [m for (m,) in conn.execute("SELECT mid FROM notes ORDER BY id")]
            dids = [d for (d,) in conn.execute("SELECT DISTINCT did FROM cards")]
            conn.close()
            return guids, mids, dids

        first = build_and_read("first.apkg", "A {{c1::typo}} here.")
        second = build_and_read("second.apkg", "A {{c1::fixed}} here.")
        assert first == second
        assert len(set(first[0])) == 3


# ---------------------------------------------------------------------------
# AnkiConnect client
# ---------------------------------------------------------------------------


class TestAnkiConnect:
    def _stub(self, monkeypatch: pytest.MonkeyPatch, reply: dict) -> list:
        sent = []

        def fake_urlopen(request, timeout=None):
            sent.append(json.loads(request.data))
            return io.BytesIO(json.dumps(reply).encode("utf-8"))

        monkeypatch.setattr(ankiconnect.urllib.request, "urlopen", fake_urlopen)
        return sent

    def test_import_package_sends_absolute_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        sent = self._stub(monkeypatch, {"result": True, "error": None})
        monkeypatch.chdir(tmp_path)
        ankiconnect.import_package(Path("deck.apkg"))
        assert sent == [
            {
                "action": "importPackage",
                "version": 6,
                "params": {"path": str((tmp_path / "deck.apkg").resolve())},
            }
        ]

    def test_error_reply_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub(monkeypatch, {"result": None, "error": "boom"})
        with pytest.raises(ankiconnect.AnkiConnectError, match="boom"):
            ankiconnect.import_package(Path("deck.apkg"))

    def test_false_result_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub(monkeypatch, {"result": False, "error": None})
        with pytest.raises(ankiconnect.AnkiConnectError, match="did not accept"):
            ankiconnect.import_package(Path("deck.apkg"))

    def test_unreachable_raises_helpful_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(request, timeout=None):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(ankiconnect.urllib.request, "urlopen", refuse)
        with pytest.raises(ankiconnect.AnkiConnectError, match="Is Anki running"):
            ankiconnect.import_package(Path("deck.apkg"))


# ---------------------------------------------------------------------------
# Schema v2
# ---------------------------------------------------------------------------


class TestSchemaV2:
    def _write(self, tmp: Path, data: dict) -> Path:
        path = tmp / "deck.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_parses_qa_mcq_cloze_and_retired(self, tmp_path: Path) -> None:
        data = {
            "schema_version": 2,
            "cards": [
                {"type": "qa", "id": "q1", "kind": "recall", "prompt": "P?", "answer": "A",
                 "grading": ["g"], "explanation": "why it holds", "source": "Book 1.1"},
                {"type": "mcq", "id": "m1", "kind": "discriminate", "question": "Which?",
                 "answer": "right", "distractors": [{"text": "w1", "why_wrong": "no"}, "w2"],
                 "explanation": "since."},
                {"type": "cloze", "id": "c1", "text": "{{c1::X}} rule", "explanation": "e",
                 "guid": "pinned-guid"},
            ],
            "retired": [{"type": "cloze", "guid": "old", "text": "{{c1::Old}}", "extra": "[x]"}],
        }
        cards = parse_json(self._write(tmp_path, data))
        assert isinstance(cards[0], BasicCard) and cards[0].grading == ["g"]
        assert isinstance(cards[1], MCQCard) and cards[1].choices[0] == "right"
        assert cards[1].distractor_notes == {"w1": "no"}
        assert cards[2].guid == "pinned-guid"
        assert "kunyi::retired" in cards[3].tags

    def test_explicit_guid_overrides_id(self) -> None:
        deck = AnkiCardDeck("D")
        card = ClozeCard(text="{{c1::A}}.", note_id="x", guid="abc123")
        assert deck._note_guid(card, "cloze") == "abc123"

    def test_kind_tag_added_and_invalid_kind_rejected(self) -> None:
        card = BasicCard(front="Q", back="A", kind="explain", tags=["t"])
        assert AnkiCardDeck._note_tags(card) == ["t", "kind::explain"]
        with pytest.raises(ValueError, match="kind"):
            BasicCard(front="Q", back="A", kind="memorise")

    def test_v1_cloze_extra_unchanged(self, valid_cloze: ClozeCard) -> None:
        assert AnkiCardDeck._format_cloze_extra(valid_cloze) == valid_cloze.extra

    def test_basic_back_orders_answer_before_feedback(self) -> None:
        card = BasicCard(front="Q", back="ANSWER", grading=["G"], explanation="WHY", source="S")
        html_back = AnkiCardDeck._format_basic_back(card)
        assert html_back.index("ANSWER") < html_back.index("G") < html_back.index("WHY")


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


class TestLint:
    def _rules(self, entry: dict) -> set[str]:
        from kunyi.lint import lint_entries

        return {f.rule for f in lint_entries([("f.json", entry)])}

    def test_clean_qa_card_has_no_findings(self) -> None:
        entry = {"type": "qa", "id": "a", "prompt": "Why is ker(T) a subspace?",
                 "answer": "Closed under + and scalar multiples.",
                 "explanation": "Linearity preserves T(v)=0 under both operations."}
        assert self._rules(entry) == set()

    def test_citation_only_explanation(self) -> None:
        entry = {"type": "cloze", "id": "a", "text": "{{c1::Rank}} counts pivots.",
                 "extra": "[Johnston, Thm 2.1]"}
        assert "ALL-002" in self._rules(entry)

    def test_cloze_leak_and_restatement(self) -> None:
        entry = {"type": "cloze", "id": "a",
                 "text": "The coordinate vector is {{c1::unique}} -- it is unique because B is independent.",
                 "explanation": "Two representations would give a dependence relation."}
        rules = self._rules(entry)
        assert {"CLZ-001", "CLZ-013"} <= rules

    def test_tex_brace_collision(self) -> None:
        entry = {"type": "cloze", "id": "a", "text": "\\(A = {{c1::PDP^{-1}}}\\)",
                 "explanation": "Diagonalisation factors A through its eigenbasis."}
        assert "CLZ-014" in self._rules(entry)

    def test_mcq_length_and_justification_cues(self) -> None:
        entry = {"type": "mcq", "id": "a", "question": "Must T be surjective?",
                 "answer": "Yes -- by rank-nullity the range has full dimension in W",
                 "distractors": ["No", "Only if V = W", "Never"],
                 "explanation": "Nullity zero forces rank equal to dim W."}
        assert {"MCQ-010", "MCQ-012"} <= self._rules(entry)

    def test_duplicate_ids(self) -> None:
        from kunyi.lint import lint_entries

        e = {"type": "qa", "id": "same", "prompt": "Why X?", "answer": "Y",
             "explanation": "Long enough explanation."}
        rules = {f.rule for f in lint_entries([("a.json", e), ("b.json", dict(e))])}
        assert "ALL-003" in rules
