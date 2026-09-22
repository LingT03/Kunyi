# kunyi

Generate Anki `.apkg` decks from JSON (MCQ, cloze) or TSV (basic) card data.

_Part of the [Seya](https://github.com/LingT03/Seya) study ecosystem._

---

## Installation

```bash
pip install kunyi
```

Or for local development, in a virtual environment:

```bash
git clone https://github.com/LingT03/Kunyi.git
cd Kunyi
python -m venv .venv # recommended for isolation
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

---

## CLI usage

break down

kunyi takes a deck name and a path to a JSON or TSV file containing card data, and produces an Anki `.apkg` file.

```bash
# JSON — multiple-choice question cards
kunyi "Cloud Computing" cards.json

# TSV — basic front/back cards
kunyi "Calc 2 Formulas" formulas.tsv

# Explicit output path (recommended for subprocess callers)
kunyi "Cloud Computing" cards.json --output /path/to/deck.apkg

# Override format detection
kunyi "My Deck" cards.data --format tsv

# Raise Anki's daily new/review card limits for a cram session
kunyi "Exam 1 Cram" cards.json --preset exam_sprint
```

On success the resolved `.apkg` path is printed to stdout (exit 0).  
On failure a human-readable message is printed to stderr (exit 1).

---

## Input formats

### JSON — MCQ and cloze cards

A JSON file is a `"cards"` array. Each entry has an optional `"type"` field —
`"mcq"` (the default, so existing files without a `"type"` key keep working
unchanged) or `"cloze"`. Both types can be mixed in the same file.

```json
{
  "cards": [
    {
      "type": "mcq",
      "question": "What does CPU stand for?",
      "choices": [
        "Central Processing Unit",
        "Core Power Unit",
        "Control Processing Unit"
      ],
      "correct_answer": "Central Processing Unit",
      "explanation": "CPU stands for Central Processing Unit.",
      "tags": ["chapter-1"]
    },
    {
      "type": "cloze",
      "text": "The mitochondria is the {{c1::powerhouse}} of the {{c2::cell}}.",
      "extra": "Mitochondria generate ATP via oxidative phosphorylation.",
      "tags": ["biology", "chapter-2"]
    }
  ]
}
```

**MCQ fields:** `correct_answer` must be an element of `choices` — validated
on parse. `tags` is optional.

**Cloze fields:** `text` must contain at least one deletion using Anki's
syntax — `{{c1::answer}}`, or `{{c1::answer::hint}}` for a hint. Use `c1`,
`c2`, etc. for multiple deletions in one card; they're revealed together as
one note but reviewed as separate cards. `extra` is optional context shown
on the answer side. `tags` is optional.

Malformed cards (missing a required field, `correct_answer` not in
`choices`, `text` with no `{{cN::...}}` deletion, or empty `front`/`back`/
`question`/`text`) are rejected with a clear error at parse time rather than
silently producing a broken card.

### TSV — basic cards

Tab-delimited UTF-8. Optional header row (`front\tback`) is auto-detected and skipped.

```
front	back
What is spaced repetition?	A technique that spaces reviews over time.
What is active recall?	Actively retrieving information from memory.
```

---

## Deck-options presets (cram sessions)

Anki caps new cards at 20/day and reviews at 100/day per deck by default.
That's the right default for steady long-term retention, but it actively
works against cramming: a deck generated the night before an exam will sit
mostly hidden behind that cap instead of being fully reviewable.

`--preset exam_sprint` raises both limits to effectively unlimited (9999) on
the deck being built:

```bash
kunyi "Exam 1 Cram" cards.json --preset exam_sprint
```

This only affects the generated deck's options — it doesn't touch Anki's
global settings or any other deck. You can always readjust or reset the
limits from **Deck Options** inside Anki after importing.

`genanki` (the library `kunyi` builds on) has no public API for deck
options, so this works by editing the finished `.apkg`'s embedded SQLite
database directly after building — see `kunyi/presets.py` if you want to
add another preset.

---

## Generating a JSON deck with an LLM

The JSON format above is easy to produce by prompting an LLM directly on your source material (notes, a textbook chapter, a PDF transcript). Paste something like this, with your own source material and topic:

```
Generate Anki flashcards as JSON from the material below.

Output ONLY valid JSON, no markdown code fences, matching this exact schema:

{
  "cards": [
    {
      "question": "string",
      "choices": ["string", "string", "..."],
      "correct_answer": "string",
      "explanation": "string",
      "tags": ["string", "..."]
    }
  ]
}

Rules:
- "correct_answer" must be copied verbatim from one of the entries in "choices".
- Each card needs 3-5 "choices".
- "explanation" should justify the correct answer in 1-2 sentences.
- "tags" is optional; use short topic labels (e.g. "chapter-3").
- Generate one card per distinct concept — don't pad with trivial questions.

Topic: <your topic>
Source material:
<paste your notes / textbook excerpt / transcript here>
```

Save the model's output to a `.json` file and run it straight through `kunyi`:

```bash
kunyi "My Deck" cards.json
```

`correct_answer` not matching an entry in `choices` verbatim is the most common failure mode — `kunyi` will reject the card with a `ValueError` at parse time rather than silently dropping it.

For dense factual material (definitions, mechanisms, derivation steps), ask the LLM for cloze cards instead — replace the schema and rules above with the `"type": "cloze"` schema from [Input formats](#input-formats), and instruct it to wrap the key term/value in `{{c1::...}}` (using `c2`, `c3`, etc. for additional deletions in the same card) rather than writing a question/answer pair.

---

## Library usage

```python
from pathlib import Path
from kunyi import AnkiCardDeck, MCQCard, BasicCard, ClozeCard

deck = AnkiCardDeck(deck_name="My Deck")

deck.add_card(MCQCard(
    question="What does RAM stand for?",
    choices=["Random Access Memory", "Read Access Module"],
    correct_answer="Random Access Memory",
    explanation="RAM is the primary short-term memory of a computer.",
    tags=["hardware"],
))

deck.add_card(BasicCard(
    front="What is a CPU?",
    back="The central processing unit — the brain of a computer.",
))

deck.add_card(ClozeCard(
    text="The {{c1::CPU}} executes instructions; {{c2::RAM}} holds data being worked on.",
    extra="Both are core components of the von Neumann architecture.",
    tags=["hardware"],
))

# `preset="exam_sprint"` raises Anki's daily new/review card limits — see
# "Deck-options presets" above.
deck.save_deck(Path("output/my_deck.apkg"), preset="exam_sprint")
```

---

## Running tests

With the `dev` extra installed (see Installation above):

```bash
pytest tests/
```

---

## What is Anki?

[Anki](https://apps.ankiweb.net/) is a free flashcard program that uses spaced repetition and active recall to maximise long-term retention. `kunyi` generates `.apkg` files that can be imported directly into Anki.
