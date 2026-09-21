# kunyi

Generate Anki `.apkg` decks from JSON (MCQ) or TSV (basic) card data.

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
```

On success the resolved `.apkg` path is printed to stdout (exit 0).  
On failure a human-readable message is printed to stderr (exit 1).

---

## Input formats

### JSON — MCQ cards

```json
{
  "cards": [
    {
      "question": "What does CPU stand for?",
      "choices": [
        "Central Processing Unit",
        "Core Power Unit",
        "Control Processing Unit"
      ],
      "correct_answer": "Central Processing Unit",
      "explanation": "CPU stands for Central Processing Unit.",
      "tags": ["chapter-1"]
    }
  ]
}
```

`correct_answer` must be an element of `choices` — validated on parse.  
`tags` is optional.

### TSV — basic cards

Tab-delimited UTF-8. Optional header row (`front\tback`) is auto-detected and skipped.

```
front	back
What is spaced repetition?	A technique that spaces reviews over time.
What is active recall?	Actively retrieving information from memory.
```

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

---

## Library usage

```python
from pathlib import Path
from kunyi import AnkiCardDeck, MCQCard, BasicCard

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

deck.save_deck(Path("output/my_deck.apkg"))
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
