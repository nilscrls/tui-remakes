import datetime
import requests
import os
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Header, Footer, Static, Input
from textual.binding import Binding

# Constants
WORD_LIST_URL = "https://raw.githubusercontent.com/LouanBen/wordle-fr/main/mots.txt"
LOCAL_FILE = Path("mots_cache.txt")
START_DATE = datetime.date(2022, 1, 9)

def get_daily_word():
    """Fetches the word list from cache or URL, then picks the daily word."""
    if not LOCAL_FILE.exists():
        try:
            response = requests.get(WORD_LIST_URL, timeout=5)
            response.raise_for_status()
            LOCAL_FILE.write_text(response.text, encoding="utf-8")
        except Exception:
            return "POMME"  # Emergency fallback

    words = [w.strip().upper() for w in LOCAL_FILE.read_text(encoding="utf-8").splitlines() if len(w.strip()) == 5]
    days_passed = (datetime.date.today() - START_DATE).days
    return words[days_passed % len(words)]

class LetterTile(Static):
    def set_state(self, char, state):
        self.update(char)
        self.remove_class("correct", "present", "absent")
        self.add_class(state)

class Key(Static):
    def set_state(self, state):
        # Hierarchy: correct > present > absent
        if "correct" in self.classes: return
        if "present" in self.classes and state == "absent": return
        
        self.remove_class("correct", "present", "absent")
        self.add_class(state)

class WordleGame(App):
    CSS = """
    #grid { align: center middle; margin: 1 0; }
    .row { height: 3; align: center middle; }
    
    LetterTile {
        width: 5; height: 3; border: solid white;
        margin: 0 1; content-align: center middle;
        text-style: bold; background: $surface;
    }

    #keyboard { align: center middle; margin-top: 1; height: 10; }
    .kb-row { align: center middle; height: 3; }
    Key {
        width: 4; height: 3; background: $panel;
        border: solid white; margin: 0 1;
        content-align: center middle; text-style: bold;
    }

    .correct { background: #6aaa64; border: none; color: white; }
    .present { background: #c9b458; border: none; color: white; }
    .absent { background: #3a3a3c; border: none; color: #818384; }
    
    #input_area { dock: bottom; height: 5; align: center middle; }
    Input { width: 30; border: double $accent; }
    """

    BINDINGS = [Binding("ctrl+c", "quit", "Quitter")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="grid"):
            for r in range(6):
                with Horizontal(classes="row"):
                    for c in range(5):
                        yield LetterTile("", id=f"tile-{r}-{c}")
        
        with Vertical(id="keyboard"):
            rows = ["AZERTYUIOP", "QSDFGHJKLM", "WXCVBN"]
            for row in rows:
                with Horizontal(classes="kb-row"):
                    for char in row:
                        yield Key(char, id=f"key-{char}")

        with Vertical(id="input_area"):
            yield Input(placeholder="Entrez un mot...", max_length=5)
        yield Footer()

    def on_mount(self) -> None:
        self.target_word = get_daily_word()
        self.current_row = 0
        self.history = [] # To store emoji results
        self.theme = "tokyo-night"
        self.title = "LE MOT - AZERTY"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        guess = event.value.upper()
        if len(guess) != 5:
            return

        self.update_game(guess)
        event.input.value = ""

    def update_game(self, guess):
        target = list(self.target_word)
        res_states = ["absent"] * 5
        guess_list = list(guess)
        emoji_row = ""

        # Pass 1: Greens
        for i in range(5):
            if guess_list[i] == target[i]:
                res_states[i] = "correct"
                target[i] = None

        # Pass 2: Yellows
        for i in range(5):
            if res_states[i] != "correct" and guess_list[i] in target:
                res_states[i] = "present"
                target[target.index(guess_list[i])] = None

        # UI Updates
        for i, state in enumerate(res_states):
            self.query_one(f"#tile-{self.current_row}-{i}", LetterTile).set_state(guess_list[i], state)
            try: self.query_one(f"#key-{guess_list[i]}", Key).set_state(state)
            except: pass
            
            # Build share string
            emoji_row += "🟩" if state == "correct" else "🟨" if state == "present" else "⬛"
        
        self.history.append(emoji_row)
        self.current_row += 1

        if guess == self.target_word:
            self.end_game(f"Gagné en {self.current_row}/6 !")
        elif self.current_row == 6:
            self.end_game(f"Perdu... Le mot était {self.target_word}")

    def end_game(self, message):
        self.notify(message, title="Fin de partie", timeout=10)
        self.query_one(Input).disabled = True
        # Print the grid to terminal on exit
        print("\n".join(self.history))

if __name__ == "__main__":
    WordleGame().run()
