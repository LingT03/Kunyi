#!/usr/bin/env python3
# DEPRECATED: this script has been superseded by the kunyi package.
# Use: kunyi <deck_name> <input_file>
# Or:  python -m kunyi <deck_name> <input_file>
# This file will be removed in a future release.

import sys
import json
import genanki
import time
import random

class AnkiCardDeck:
    def __init__(self, deck_name, deck_id=2059400110, model_id=1607392319):
        self.deck_name = deck_name
        self.deck_id = deck_id
        self.model_id = model_id
        
        # Create a custom model
        self.my_model = genanki.Model(
            self.model_id,
            'MCQ Model with Explanation',
            fields=[
                {'name': 'Question'},
                {'name': 'Answer'},
            ],
            templates=[
                {
                    'name': 'Card 1',
                    # Front: question with MC choices
                    'qfmt': '{{Question}}',
                    # Back: front side + answer
                    'afmt': '{{FrontSide}}<hr id="answer">{{Answer}}',
                },
            ]
        )
        
        # Create a deck object
        self.deck = genanki.Deck(self.deck_id, self.deck_name)

    def add_card(self, question, choices, correct_answer, explanation):
        """
        Adds a single card (front: question + numbered MC choices;
                           back: correct answer + explanation).
        """
        front_text = self.format_front(question, choices)
        back_text  = self.format_back(correct_answer, explanation)
        
        note = genanki.Note(
            model=self.my_model,
            fields=[front_text, back_text]
        )
        self.deck.add_note(note)

    @staticmethod
    def format_front(question, choices):
        """
        Returns a string that shows the question and the multiple-choice 
        options using HTML line breaks.
        """
        formatted_choices = []
        for i, choice in enumerate(choices, start=1):
            formatted_choices.append(f"{i}. {choice}")
        choices_text = "<br>".join(formatted_choices)
        
        # Return question and choices separated by <br>
        return f"{question}<br><br>{choices_text}"

    @staticmethod
    def format_back(correct_answer, explanation):
        """
        Returns a string to display on the back of the card:
        - The correct answer
        - The explanation
        """
        formatted_answer = f"<strong>Correct Answer:</strong> {correct_answer}<br><br>"
        formatted_explanation = f"<strong>Explanation:</strong> {explanation}"
        return formatted_answer + formatted_explanation

    def save_deck(self, output_filename="cards/CloudComputingFlashcards.apkg"):
        """
        Writes the deck to an .apkg file that can be imported into Anki.
        """
        package = genanki.Package(self.deck)
        package.write_to_file(output_filename)
        print(f"Anki package '{output_filename}' has been generated.")

def main():
    if len(sys.argv) < 3:
        print("Usage: python makecards.py <deck_name> <quiz_data.json>")
        sys.exit(1)

    deck_name = sys.argv[1]
    quiz_file = sys.argv[2]

    # Generate unique deck/model IDs
    unique_deck_id = int(time.time())
    unique_model_id = unique_deck_id + random.randint(1, 1000000)

    with open(quiz_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    deck = AnkiCardDeck(deck_name=deck_name, deck_id=unique_deck_id, model_id=unique_model_id)

    for card in data["cards"]:
        question = card["question"]
        choices = card["choices"]
        correct_answer = card["correct_answer"]
        explanation = card["explanation"]

        deck.add_card(question, choices, correct_answer, explanation)

    output_filename = f"cards/{deck_name.replace(' ', '_')}.apkg"
    deck.save_deck(output_filename)

if __name__ == "__main__":
    main()