import httpx
import json
import os
from datetime import datetime
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, DataTable, Label, LoadingIndicator
from textual.containers import Vertical, Horizontal
from textual.binding import Binding
import re

HISTORY_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "history.json")

headers = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Host": "cemantix.certitudes.org",
    "Origin": "https://cemantix.certitudes.org",
    "referrer": "https://cemantix.certitudes.org/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36",
}

class CemantixTUI(App):
    TITLE = "Cémantix"
    SUB_TITLE = "TUI Remake"
    
    CSS_PATH = "cemantix.tcss"


    BINDINGS = [
        Binding("q", "quit", "Quitter"),
        Binding("ctrl+l", "clear_history", "Effacer Historique"),
    ]
    url = "https://cemantix.certitudes.org/"

    def compose(self) -> ComposeResult:
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
        self.theme = "dracula"
        table = self.query_one(DataTable)
        # We assign explicit keys here to avoid the KeyError: 2
        table.add_column("Essai", key="attempt")
        table.add_column("Mot", key="word")
        table.add_column("Score (%)", key="score")
        table.add_column("Rang", key="rank")

        table.cursor_type = "row"
        self.load_history()
        self.query_one(Input).focus()
        await self.load_puzzle_number_worker()

    def load_history(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    all_days = json.load(f)
                    today = datetime.now().strftime("%Y-%m-%d")
                    today_entry = None
                    for entry in all_days:
                        if entry.get("date") == today:
                            today_entry = entry
                            break
                    if today_entry:
                        for guess in today_entry.get("guesses", []):
                            self.add_to_table(guess["word"], guess["score"], guess["rank"], save=False)
                        # Optionally, set self.puzzle_number if present
                        if "puzzle_number" in today_entry:
                            self.puzzle_number = today_entry["puzzle_number"]
            except Exception:
                pass

    def save_history(self):
        table = self.query_one(DataTable)
        guesses = []
        for row_key in table.rows:
            row = table.get_row(row_key)
            guesses.append({
                "word": row[1],
                # Clean "%" for storage
                "score": float(str(row[2]).replace('%', '')),
                "rank": int(row[3]) if str(row[3]).isdigit() else None
            })

        today = datetime.now().strftime("%Y-%m-%d")
        puzzle_number = getattr(self, "puzzle_number", None)
        new_entry = {"date": today, "puzzle_number": puzzle_number, "guesses": guesses}

        all_days = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    all_days = json.load(f)
            except Exception:
                all_days = []

        # Replace or add today's entry
        found = False
        for i, entry in enumerate(all_days):
            if entry.get("date") == today:
                all_days[i] = new_entry
                found = True
                break
        if not found:
            all_days.append(new_entry)

        with open(HISTORY_FILE, "w") as f:
            json.dump(all_days, f, indent=2)

    def action_clear_history(self):
        # Only clear today's history, not the whole file
        today = datetime.now().strftime("%Y-%m-%d")
        all_days = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    all_days = json.load(f)
            except Exception:
                all_days = []
        all_days = [entry for entry in all_days if entry.get("date") != today]
        with open(HISTORY_FILE, "w") as f:
            json.dump(all_days, f, indent=2)
        self.query_one(DataTable).clear()
        self.notify("Historique effacé")


    async def load_puzzle_number_worker(self):
        loader = self.query_one("#loader", LoadingIndicator)
        loader.display = True
        loader.visible = True
        self.query_one(Input).disabled = True
        # Start the worker and poll for completion
        self._puzzle_worker = self.run_worker(
            self.fetch_puzzle_number,
            description="Chargement du puzzle...",
            group="puzzle-number",
        )
        self._puzzle_interval = self.set_interval(0.1, self._poll_puzzle_worker)

    def _poll_puzzle_worker(self):
        worker = getattr(self, "_puzzle_worker", None)
        if worker is None:
            return
        if worker.is_finished:
            result = worker.result
            error = worker.error
            loader = self.query_one("#loader", LoadingIndicator)
            loader.display = False
            loader.visible = False
            self.query_one(Input).disabled = False
            if error:
                self.notify(f"Erreur lors du chargement du puzzle: {error}", severity="error")
            elif result:
                self.puzzle_number = result
                self.notify(f"Numéro de puzzle: {self.puzzle_number}", severity="information")
            else:
                self.notify("Numéro de puzzle introuvable.", severity="error")
            # Stop polling
            self._puzzle_worker = None
            if hasattr(self, "_puzzle_interval"):
                self._puzzle_interval.stop()
                self._puzzle_interval = None

    def on_puzzle_number_done(self, result):
        loader = self.query_one("#loader", LoadingIndicator)
        loader.display = False
        loader.visible = False
        self.query_one(Input).disabled = False
        if result:
            self.puzzle_number = result
            self.notify(f"Numéro de puzzle: {self.puzzle_number}", severity="information")
        else:
            self.notify("Numéro de puzzle introuvable.", severity="error")

    def on_puzzle_number_error(self, error):
        loader = self.query_one("#loader", LoadingIndicator)
        loader.display = False
        loader.visible = False
        self.query_one(Input).disabled = False
        self.notify(f"Erreur lors du chargement du puzzle: {error}", severity="error")

    async def fetch_puzzle_number(self):
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(self.url, headers=headers)
            if response.status_code == 200:
                match = re.search(r'data-puzzle-number="(\d+)"', response.text)
                if match:
                    return match.group(1)
            elif response.status_code == 403:
                raise Exception("Accès refusé. Vérifiez votre adresse IP.")
            else:
                raise Exception("Échec du chargement de la page.")
        return None



    def get_score_worker(self, word: str):
        # Pass the coroutine directly to run_worker, not a lambda
        self._score_worker = self.run_worker(
            self.fetch_score(word),
            description="Recherche du score...",
            group="score-worker",
        )
        self._score_interval = self.set_interval(0.1, self._poll_score_worker)

    def _poll_score_worker(self):
        worker = getattr(self, "_score_worker", None)
        if worker is None:
            return
        if worker.is_finished:
            result = worker.result
            error = worker.error
            loader = self.query_one("#loader", LoadingIndicator)
            loader.display = False
            loader.visible = False
            self.query_one(Input).disabled = False
            if error:
                self.notify(f"Erreur lors de la récupération du score: {error}", severity="error")
            elif result:
                if "e" in result:
                    self.notify(result["e"], severity="error")
                else:
                    # Score is 0-1, so we * 100
                    score_pct = result.get("s", 0) * 100
                    self.add_to_table(self._score_word, score_pct, result.get("p"))
                    if result.get("p") == 1000:
                        self.notify("🎉 BRAVO ! Vous avez trouvé le mot !", severity="information", timeout=10)
            # Stop polling
            self._score_worker = None
            if hasattr(self, "_score_interval"):
                self._score_interval.stop()
                self._score_interval = None
            self._score_word = None

    async def fetch_score(self, word: str):
        url = f"https://cemantix.certitudes.org/score?n={self.puzzle_number}"
        data = f"word={word}".encode("utf-8")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, data=data)
            return response.json()

    def add_to_table(self, word: str, score: float, rank: any, save=True):
        table = self.query_one(DataTable)
        
        # Avoid duplicates
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
            key=word # Using the word as the unique row key
        )
        
        # Sort using the 'score' column key
        # We use a lambda to ensure 9.0% doesn't come after 10.0% (string sorting)
        table.sort("score", reverse=True, key=lambda val: float(val.replace('%', '')))
        
        if save:
            self.save_history()


    async def on_input_submitted(self, event: Input.Submitted) -> None:
        word = event.value.strip().lower()
        if not word:
            return
        event.input.value = ""

        loader = self.query_one("#loader", LoadingIndicator)
        loader.display = True
        loader.visible = True
        self.query_one(Input).disabled = True
        self._score_word = word
        self.get_score_worker(word)

if __name__ == "__main__":
    app = CemantixTUI()
    app.run()
