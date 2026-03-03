"""Cémantix TUI - A semantic word guessing game"""
import httpx
import json
import re
from datetime import datetime
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, DataTable, Label, LoadingIndicator
from textual.containers import Vertical, Horizontal
from textual.binding import Binding
from textual import work

HISTORY_FILE = Path(__file__).parent / "history.json"
API_HOST = "cemantix.certitudes.org"
API_URL = f"https://{API_HOST}"
API_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Host": API_HOST,
    "Origin": API_URL,
    "Referrer": f"{API_URL}/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


class CemantixTUI(App):
    """A TUI game for Cémantix - guess words based on semantic similarity"""
    
    TITLE = "Cémantix"
    SUB_TITLE = "TUI Remake"
    CSS_PATH = "style.tcss"
    
    BINDINGS = [
        Binding("q", "quit", "Quitter"),
        Binding("ctrl+l", "clear_history", "Effacer Historique"),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.puzzle_number = None

    def compose(self) -> ComposeResult:
        """Create the UI layout"""
        yield Header()
        with Vertical(id="main-container"):
            yield Label("Trouvez le mot secret du jour :")
            with Horizontal(id="input-area"):
                yield Input(placeholder="Entrez un mot...", id="word-input")
                yield LoadingIndicator(id="loader", classes="cemantix-loader")
            with Horizontal(id="loader-table-row"):
                yield DataTable(zebra_stripes=True, id="results-table")
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize the application"""
        self.theme = "dracula"
        
        # Setup table
        table = self.query_one(DataTable)
        table.add_column("Essai", key="attempt")
        table.add_column("Mot", key="word")
        table.add_column("Score (%)", key="score")
        table.add_column("Rang", key="rank")
        table.cursor_type = "row"
        
        # Load history and puzzle
        self._load_history()
        self.query_one(Input).focus()
        self._fetch_puzzle_number()

    # ============ History Management ============

    def _load_history(self) -> None:
        """Load today's history from disk"""
        if not HISTORY_FILE.exists():
            return
            
        try:
            all_days = json.loads(HISTORY_FILE.read_text())
            today = datetime.now().strftime("%Y-%m-%d")
            
            for entry in all_days:
                if entry.get("date") == today:
                    for guess in entry.get("guesses", []):
                        self._add_to_table(
                            guess["word"], 
                            guess["score"], 
                            guess["rank"], 
                            save=False
                        )
                    if "puzzle_number" in entry:
                        self.puzzle_number = entry["puzzle_number"]
                    break
        except Exception:
            pass

    def _save_history(self) -> None:
        """Save current session to history file"""
        table = self.query_one(DataTable)
        guesses = []
        
        for row_key in table.rows:
            row = table.get_row(row_key)
            guesses.append({
                "word": row[1],
                "score": float(str(row[2]).replace('%', '')),
                "rank": int(row[3]) if str(row[3]).isdigit() else None
            })

        today = datetime.now().strftime("%Y-%m-%d")
        new_entry = {
            "date": today,
            "puzzle_number": self.puzzle_number,
            "guesses": guesses
        }

        # Load existing history
        all_days = []
        if HISTORY_FILE.exists():
            try:
                all_days = json.loads(HISTORY_FILE.read_text())
            except Exception:
                all_days = []

        # Update or append today's entry
        for i, entry in enumerate(all_days):
            if entry.get("date") == today:
                all_days[i] = new_entry
                break
        else:
            all_days.append(new_entry)

        HISTORY_FILE.write_text(json.dumps(all_days, indent=2))

    def action_clear_history(self) -> None:
        """Clear today's history (ctrl+l)"""
        today = datetime.now().strftime("%Y-%m-%d")
        
        all_days = []
        if HISTORY_FILE.exists():
            try:
                all_days = json.loads(HISTORY_FILE.read_text())
            except Exception:
                pass
        
        all_days = [entry for entry in all_days if entry.get("date") != today]
        HISTORY_FILE.write_text(json.dumps(all_days, indent=2))
        
        self.query_one(DataTable).clear()
        self.notify("Historique effacé")

    # ============ Puzzle Number Fetching ============

    @work(exclusive=True, group="fetch")
    async def _fetch_puzzle_number(self) -> None:
        """Fetch the current puzzle number from the website"""
        loader = self.query_one("#loader", LoadingIndicator)
        input_widget = self.query_one(Input)
        
        loader.display = True
        input_widget.disabled = True
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(API_URL, headers=API_HEADERS)
                
                if response.status_code == 403:
                    self.notify("Accès refusé. Vérifiez votre adresse IP.", severity="error")
                    return
                    
                if response.status_code != 200:
                    self.notify("Échec du chargement de la page.", severity="error")
                    return
                
                match = re.search(r'data-puzzle-number="(\d+)"', response.text)
                if match:
                    self.puzzle_number = match.group(1)
                    self.notify(f"Puzzle #{self.puzzle_number}", severity="information")
                else:
                    self.notify("Numéro de puzzle introuvable.", severity="error")
                    
        except httpx.TimeoutException:
            self.notify("Délai d'attente dépassé.", severity="error")
        except Exception as e:
            self.notify(f"Erreur: {e}", severity="error")
        finally:
            loader.display = False
            input_widget.disabled = False

    # ============ Word Scoring ============

    @work(exclusive=True, group="score")
    async def _fetch_score(self, word: str) -> None:
        """Fetch the score for a guessed word"""
        loader = self.query_one("#loader", LoadingIndicator)
        input_widget = self.query_one(Input)
        
        loader.display = True
        input_widget.disabled = True
        
        try:
            url = f"{API_URL}/score?n={self.puzzle_number}"
            data = f"word={word}".encode("utf-8")
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, headers=API_HEADERS, data=data)
                result = response.json()
            
            if "e" in result:
                self.notify(result["e"], severity="error")
            else:
                score_pct = result.get("s", 0) * 100
                rank = result.get("p")
                
                self._add_to_table(word, score_pct, rank)
                
                if rank == 1000:
                    self.notify(
                        "🎉 BRAVO ! Vous avez trouvé le mot !",
                        severity="information",
                        timeout=10
                    )
                    
        except httpx.TimeoutException:
            self.notify("Délai d'attente dépassé.", severity="error")
        except Exception as e:
            self.notify(f"Erreur lors de la récupération du score: {e}", severity="error")
        finally:
            loader.display = False
            input_widget.disabled = False

    # ============ Table Management ============

    def _add_to_table(self, word: str, score: float, rank: int | None, save: bool = True) -> None:
        """Add a word result to the table"""
        table = self.query_one(DataTable)
        
        # Check for duplicates
        for row_key in table.rows:
            if table.get_row(row_key)[1] == word:
                return

        attempt_num = len(table.rows) + 1
        rank_val = str(rank) if rank is not None else "---"
        
        table.add_row(
            str(attempt_num),
            word,
            f"{score:.2f}%",
            rank_val,
            key=word
        )
        
        # Sort by score descending
        table.sort("score", reverse=True, key=lambda val: float(val.replace('%', '')))
        
        if save:
            self._save_history()

    # ============ Event Handlers ============

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle word submission"""
        word = event.value.strip().lower()
        
        if not word:
            return
            
        if not self.puzzle_number:
            self.notify("Puzzle non chargé. Veuillez patienter.", severity="warning")
            return
        
        event.input.value = ""
        self._fetch_score(word)


if __name__ == "__main__":
    app = CemantixTUI()
    app.run()
