"""Genanki model factories for each card type.

Each factory returns a configured genanki.Model. Keeping model construction
here isolates the Anki-specific template/field schema from deck orchestration
in deck.py. To add a new card type, add a new factory here and register it
in deck.py's dispatch logic.
"""

import genanki


def basic_model(model_id: int) -> genanki.Model:
    """Return a genanki Model for BasicCard (front / back).

    Parameters
    ----------
    model_id:
        Unique integer ID for the model. Caller is responsible for stability
        across regenerations so Anki can match notes to the correct model.

    Returns
    -------
    genanki.Model
        A two-field model rendering Front on the question side and Back on
        the answer side.
    """
    return genanki.Model(
        model_id,
        "Basic",
        fields=[
            {"name": "Front"},
            {"name": "Back"},
        ],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": "{{FrontSide}}<hr id=answer>{{Back}}",
            },
        ],
    )


def mcq_model(model_id: int) -> genanki.Model:
    """Return a genanki Model for MCQCard (question + choices / answer + explanation).

    Parameters
    ----------
    model_id:
        Unique integer ID for the model.

    Returns
    -------
    genanki.Model
        A two-field model where the Question field contains the formatted
        question + numbered choices and the Answer field contains the correct
        answer and explanation.
    """
    return genanki.Model(
        model_id,
        "MCQ with explanation",
        fields=[
            {"name": "Question"},
            {"name": "Answer"},
        ],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Question}}",
                "afmt": "{{FrontSide}}<hr id=answer>{{Answer}}",
            },
        ],
    )
