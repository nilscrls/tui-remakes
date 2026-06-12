"""
Mots Croisés TUI - Remake of https://jeux.franceinfo.fr/mots-croises/

Récupère la grille du jour (fournie par mygamify.fr) et la fait jouer dans
le terminal, à l'identique du site :
  - un clic sur une case sélectionne son mot horizontal,
  - un second clic (re-clic) bascule sur le mot vertical,
  - écrire une lettre la place dans la case courante puis avance jusqu'à la
    prochaine case non verrouillée du mot,
  - une case se verrouille (en vert) dès que le mot qu'elle compose est trouvé.

La validation des mots est faite côté serveur (mêmes appels que le site), donc
les réponses ne sont jamais stockées en clair côté client.
"""
import asyncio
import base64
import codecs
import json
import re
from datetime import date
from pathlib import Path

import httpx
from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Label, OptionList, Static
from textual.widgets.option_list import Option

# ============ Source de données (jeux.franceinfo.fr -> sdk.mygamify.fr) ============

GAMIFY_BASE = "https://sdk.mygamify.fr"
CLIENT_ID = "K6GLoEjoe5"
API_KEY = "u4PUvZHm8yE7La5hZaYgmfmlsR7XJWBo"
STYLESHEET = "https://sdk.mygamify.fr/examples/franceinfo/style.css"
REFERER = "https://jeux.franceinfo.fr/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Liste des grilles publiées (numéro + date) : page courante + archives.
FRANCEINFO_PAGES = (
    "https://jeux.franceinfo.fr/mots-croises/classique/",
    "https://jeux.franceinfo.fr/mots-croises/classique/archives/",
)
ARCHIVE_RE = re.compile(
    r"mots-croises-niveau-classique-(\d{2})-(\d{2})-(\d{4})-grille-(\d+)_\d+\.html"
)


async def fetch_grid_list(client: httpx.AsyncClient) -> list[dict]:
    """Liste les grilles disponibles : [{number, date(YYYY-MM-DD), display}]."""
    headers = {"User-Agent": USER_AGENT}

    async def get(url: str) -> str:
        try:
            resp = await client.get(url, headers=headers, follow_redirects=True)
            return resp.text if resp.status_code == 200 else ""
        except Exception:
            return ""

    pages = await asyncio.gather(*(get(u) for u in FRANCEINFO_PAGES))
    by_number: dict[int, dict] = {}
    for text in pages:
        for dd, mm, yyyy, num in ARCHIVE_RE.findall(text):
            n = int(num)
            by_number[n] = {
                "number": n,
                "date": f"{yyyy}-{mm}-{dd}",
                "display": f"{dd}/{mm}/{yyyy}",
            }
    return sorted(by_number.values(), key=lambda e: e["number"], reverse=True)

# Fichiers locaux : progression, cache des grilles, cache de la liste du jour.
SAVES_FILE = Path(__file__).parent / "saves.json"
GRIDS_CACHE_FILE = Path(__file__).parent / "grids_cache.json"
GRIDLIST_CACHE_FILE = Path(__file__).parent / "gridlist_cache.json"


def _read_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _write_json(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def _read_saves() -> dict:
    return _read_json(SAVES_FILE)


def _write_saves(data: dict) -> None:
    _write_json(SAVES_FILE, data)


def _data_param(date_id: str) -> str:
    """Recrée le paramètre `data` (base64 d'un JSON d'options) attendu par le SDK."""
    opts = {
        "id": date_id,
        "slug": "crosswords",
        "template": "2",
        "difficulty": "easy",
        "clientID": CLIENT_ID,
        "apiKey": API_KEY,
        "stylesheet": STYLESHEET,
        "authToken": "",
    }
    return base64.b64encode(json.dumps(opts).encode()).decode()


def _decode_js_string(raw: str) -> str:
    """Décode les échappements \\uXXXX présents dans les littéraux du SDK."""
    return codecs.decode(raw, "unicode_escape")


async def fetch_puzzle(client: httpx.AsyncClient, date_id: str) -> dict | None:
    """Récupère et parse la grille d'une date donnée. Renvoie None si absente."""
    data = _data_param(date_id)
    url = f"{GAMIFY_BASE}/mots-croises/jouer/{date_id}?data={data}"
    headers = {"Referer": REFERER, "User-Agent": USER_AGENT}

    resp = await client.get(url, headers=headers, follow_redirects=True)
    if resp.status_code != 200:
        return None
    html = resp.text

    m = re.search(r"APP_GAME_DATA\s*=\s*JSON\.parse\('(.*?)'\)\s*;", html, re.S)
    if not m:
        return None
    game = json.loads(_decode_js_string(m.group(1)))
    if not game.get("grid"):
        return None

    check_path = re.search(r"APP_CHECK_URL\s*=\s*'([^']*)'", html)
    if not check_path:
        return None
    check_url = f"{GAMIFY_BASE}{check_path.group(1)}?iframe=&origin=&data={data}"

    return {
        "date": date_id,
        "rows": int(game["rows"]),
        "cols": int(game["columns"]),
        "grid": game["grid"],
        "level": game.get("level", 1),
        "discovery": 1 if game.get("discovery") else 0,
        "next_id": game.get("next_grid_id") or 0,
        "check_url": check_url,
    }


async def check_words(client: httpx.AsyncClient, puzzle: dict, filled: list[dict]) -> dict:
    """Valide côté serveur les mots remplis. Renvoie la réponse brute.

    Réponse normale : ``{"errors": N, "validated": [{id, word, axe}, ...]}``.
    Réponse de complétion (grille entièrement correcte) : pas de clé
    ``validated`` mais ``{"code": true, "points": ...}``.
    """
    payload = {
        "time": 0,
        "is_discovery": puzzle["discovery"],
        "grid": json.dumps(filled),
        "nextId": puzzle["next_id"],
        "level": puzzle["level"],
        "usedHelps": "[]",
    }
    headers = {"Referer": f"{GAMIFY_BASE}/", "User-Agent": USER_AGENT}
    resp = await client.post(puzzle["check_url"], data=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()


# ============ Modèle de grille ============


class Word:
    """Un mot de la grille : ses cases, sa définition, son orientation."""

    def __init__(self, wid, axe, cells, definition, label):
        self.id = str(wid)
        self.axe = axe  # 'x' (horizontal) ou 'y' (vertical)
        self.cells = cells  # liste de (row, col)
        self.definition = definition
        self.label = label  # lettre de ligne (H) ou numéro de colonne (V)
        self.found = False
        self.wrong = False  # rempli + vérifié mais incorrect (jusqu'à modif.)


class Board:
    """État de la grille : lettres saisies, mots, cases noires, verrous."""

    def __init__(self, puzzle: dict):
        self.rows = puzzle["rows"]
        self.cols = puzzle["cols"]
        self.letters = [["" for _ in range(self.cols)] for _ in range(self.rows)]
        self.white: set[tuple[int, int]] = set()
        self.words: list[Word] = []
        self.by_id: dict[str, Word] = {}
        # cell -> {'x': Word|None, 'y': Word|None}
        self.cell_words: dict[tuple[int, int], dict] = {}

        for w in puzzle["grid"]:
            r0, c0 = int(w["row"]) - 1, int(w["column"]) - 1
            axe = "x" if w["sens"].upper() == "H" else "y"
            n = int(w["size_word"])
            cells = []
            for j in range(n):
                rc = (r0, c0 + j) if axe == "x" else (r0 + j, c0)
                cells.append(rc)
                self.white.add(rc)
                self.cell_words.setdefault(rc, {"x": None, "y": None})
            label = chr(65 + r0) if axe == "x" else str(c0 + 1)
            word = Word(w["id"], axe, cells, w["def"], label)
            self.words.append(word)
            self.by_id[word.id] = word
            for rc in cells:
                self.cell_words[rc][axe] = word

    def is_white(self, r, c):
        return (r, c) in self.white

    def cell_locked(self, r, c):
        """Une case est verrouillée si l'un de ses mots est trouvé."""
        cw = self.cell_words.get((r, c))
        if not cw:
            return False
        return (cw["x"] and cw["x"].found) or (cw["y"] and cw["y"].found)

    def cell_wrong(self, r, c):
        """Une case est « erronée » si l'un de ses mots a été vérifié faux."""
        cw = self.cell_words.get((r, c))
        if not cw:
            return False
        return any(cw[a] and cw[a].wrong and not cw[a].found for a in ("x", "y"))

    def clear_wrong(self, r, c):
        """Efface l'état « erroné » des mots passant par une case modifiée."""
        cw = self.cell_words.get((r, c))
        if cw:
            for a in ("x", "y"):
                if cw[a]:
                    cw[a].wrong = False

    def word_through(self, r, c, axe) -> Word | None:
        cw = self.cell_words.get((r, c))
        if not cw:
            return None
        return cw[axe] or cw["x"] or cw["y"]

    def filled_words(self) -> list[dict]:
        """Mots entièrement remplis, au format attendu par le validateur."""
        out = []
        for w in self.words:
            letters = [self.letters[r][c] for (r, c) in w.cells]
            if all(letters):
                out.append({"id": w.id, "word": "".join(letters), "axe": w.axe})
        return out

    def all_found(self) -> bool:
        return all(w.found for w in self.words)


# ============ Widget grille (rendu + clics) ============

# Géométrie de la grille dessinée (cases carrées, bordures complètes).
#   ligne 0           : numéros de colonnes
#   ligne 1           : bordure haute
#   lignes paires >=2 : contenu d'une rangée   (rangée r -> ligne 2 + 2*r)
#   lignes impaires   : séparateurs / bordure basse
LABEL_W = 2   # gouttière de gauche (lettre de rangée + espace)
CELL_W = 3    # largeur intérieure d'une case (" X ")
STRIDE_X = CELL_W + 1  # case + bordure verticale
PAD = " " * LABEL_W


def _cell_center_x(col: int) -> int:
    """Colonne (en caractères) du centre de la case `col`."""
    return LABEL_W + 2 + STRIDE_X * col


def _cell_center_y(row: int) -> int:
    """Ligne (en caractères) du centre de la case `row`."""
    return 2 + 2 * row


class GridView(Static):
    """Affiche la grille et convertit les clics en coordonnées de case."""

    def on_click(self, event) -> None:
        self.app.on_grid_click(event.x, event.y)


class ClueItem(Static):
    """Une définition cliquable : sélectionne son mot dans la grille."""

    def __init__(self, word: "Word", **kwargs):
        super().__init__(**kwargs)
        self.word = word

    def on_click(self) -> None:
        self.app.select_word(self.word)


class GridPickerScreen(ModalScreen):
    """Sélecteur de grille (numéro + date), avec état de progression."""

    BINDINGS = [Binding("escape", "cancel", "Annuler")]

    def __init__(self, entries: list[dict], saves: dict):
        super().__init__()
        self.entries = entries
        self.saves = saves

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Label("Choisir une grille", id="picker-title")
            yield OptionList(id="picker-list")
            yield Label("↑↓ naviguer · Entrée jouer · Échap annuler", id="picker-hint")

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        for e in self.entries:
            save = self.saves.get(str(e["number"]))
            status = ""
            if save:
                found = len(save.get("found", []))
                total = save.get("total")
                if total and found >= total:
                    status = "  ✓ terminée"
                elif total:
                    status = f"  · {found}/{total}"
                else:
                    status = "  · en cours"
            label = f"N° {e['number']}  —  {e['display']}{status}"
            option_list.add_option(Option(label, id=str(e["number"])))
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        num = int(event.option.id)
        entry = next(e for e in self.entries if e["number"] == num)
        self.dismiss(entry)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ============ Application ============


class MotsCroisesTUI(App):
    """Mots croisés du jour de franceinfo, jouables au terminal."""

    TITLE = "Mots Croisés"
    SUB_TITLE = "franceinfo · TUI Remake"
    CSS_PATH = "mots-croises.tcss"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quitter"),
        Binding("up", "move(-1,0)", "Haut", show=False),
        Binding("down", "move(1,0)", "Bas", show=False),
        Binding("left", "move(0,-1)", "Gauche", show=False),
        Binding("right", "move(0,1)", "Droite", show=False),
        Binding("space", "toggle_direction", "Sens H/V"),
        Binding("backspace", "backspace", "Effacer", show=False),
        Binding("tab", "next_word", "Mot suivant"),
        Binding("ctrl+g", "choose_grid", "Grilles"),
    ]

    # Actions de jeu désactivées quand un écran modal est ouvert (palette, etc.).
    _GAME_ACTIONS = frozenset(
        {"move", "toggle_direction", "backspace", "next_word", "choose_grid"}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.board: Board | None = None
        self.puzzle: dict | None = None
        self.grid_list: list[dict] = []  # grilles disponibles (numéro + date)
        # Cache des grilles téléchargées (persisté sur disque, indexé par numéro).
        self.grid_cache: dict[str, dict] = _read_json(GRIDS_CACHE_FILE)
        self.cursor = (0, 0)
        self.direction = "x"
        self._last_click = None  # dernière case cliquée (pour le clic/re-clic)
        self.clue_items: dict[str, ClueItem] = {}
        self.client = httpx.AsyncClient(timeout=30)

    # ---------- Cycle de vie ----------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="grid-container"):
                yield GridView("Chargement de la grille du jour…", id="grid-view")
                yield Static(id="current-clue")
            with VerticalScroll(id="clues-container"):
                yield Label("Horizontalement", classes="clue-title")
                yield Vertical(id="across-list")
                yield Label("Verticalement", classes="clue-title")
                yield Vertical(id="down-list")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "dracula"
        # Le panneau de définitions ne doit pas capter les flèches (navigation).
        self.query_one("#clues-container").can_focus = False
        self._start()

    async def on_unmount(self) -> None:
        await self.client.aclose()

    @property
    def _modal_open(self) -> bool:
        """Vrai si un écran modal (palette de commandes, sélecteur) est ouvert."""
        return self.screen is not self.screen_stack[0]

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Neutralise les raccourcis de jeu tant qu'un écran modal est ouvert,
        # pour que les touches aillent à la palette / au sélecteur.
        if self._modal_open and action in self._GAME_ACTIONS:
            return False
        return True

    @work(exclusive=True, group="load")
    async def _start(self) -> None:
        """Récupère la liste des grilles puis charge la plus récente.

        La liste est mise en cache pour la journée : une nouvelle grille ne
        paraît qu'une fois par jour, donc rouvrir l'app le même jour évite tout
        appel réseau (la grille du jour est déjà connue et en cache).
        """
        today = date.today().isoformat()
        cached = _read_json(GRIDLIST_CACHE_FILE)
        if cached.get("date") == today and cached.get("grids"):
            self.grid_list = cached["grids"]
        else:
            self.grid_list = await fetch_grid_list(self.client)
            if self.grid_list:
                _write_json(
                    GRIDLIST_CACHE_FILE, {"date": today, "grids": self.grid_list}
                )

        if not self.grid_list:
            self.query_one("#grid-view", GridView).update(
                "Impossible de charger la liste des grilles."
            )
            self.notify("Échec du chargement.", severity="error")
            return
        await self._load_grid(self.grid_list[0])

    @work(exclusive=True, group="load")
    async def _open_grid(self, entry: dict) -> None:
        """Charge une grille choisie dans le sélecteur (worker dédié)."""
        await self._load_grid(entry)

    async def _load_grid(self, entry: dict) -> None:
        """Charge la grille `entry` ({number, date, display}) et sa sauvegarde.

        Les grilles ne changent pas : une fois téléchargée, une grille est
        gardée en cache et réutilisée sans nouvel appel réseau.
        """
        key = str(entry["number"])
        puzzle = self.grid_cache.get(key)
        if puzzle is None:
            self.query_one("#grid-view", GridView).update("Chargement de la grille…")
            try:
                puzzle = await fetch_puzzle(self.client, entry["date"])
            except Exception:
                puzzle = None
            if not puzzle:
                self.query_one("#grid-view", GridView).update(
                    "Impossible de charger cette grille."
                )
                self.notify("Échec du chargement de la grille.", severity="error")
                return
            self.grid_cache[key] = puzzle
            _write_json(GRIDS_CACHE_FILE, self.grid_cache)  # survit aux redémarrages

        # Copie superficielle : number/display dépendent de l'entrée choisie.
        puzzle = {**puzzle, "number": entry["number"], "display": entry["display"]}
        self.puzzle = puzzle
        self.board = Board(puzzle)
        self.sub_title = f"franceinfo · grille n°{entry['number']} ({entry['display']})"
        await self._build_clues()
        # Restaure la progression sauvegardée pour cette grille, le cas échéant.
        self._apply_save()
        # Curseur sur le premier mot non encore trouvé.
        first = next(
            (w for w in self.board.words if not w.found), self.board.words[0]
        )
        self.cursor = first.cells[0]
        self.direction = first.axe
        self._render_all()
        self.notify(f"Grille n°{entry['number']} chargée.", severity="information")
        # Grille déjà entièrement remplie mais pas validée : on revérifie
        # (cas d'une grille terminée lors d'une session précédente).
        if not self.board.all_found() and all(
            self.board.letters[r][c] for (r, c) in self.board.white
        ):
            self._validate()

    # ---------- Sélection de grille ----------

    def action_choose_grid(self) -> None:
        if not self.grid_list:
            return
        self.push_screen(
            GridPickerScreen(self.grid_list, _read_saves()), self._on_grid_chosen
        )

    def _on_grid_chosen(self, entry: dict | None) -> None:
        if entry and (not self.puzzle or entry["number"] != self.puzzle.get("number")):
            self._open_grid(entry)

    # ---------- Sauvegarde / reprise ----------

    def _apply_save(self) -> None:
        """Recharge lettres + mots trouvés depuis la sauvegarde de cette grille."""
        board = self.board
        entry = _read_saves().get(str(self.puzzle["number"]))
        if not entry:
            return
        saved = entry.get("letters")
        if (
            isinstance(saved, list)
            and len(saved) == board.rows
            and all(isinstance(row, list) and len(row) == board.cols for row in saved)
        ):
            # On ne restaure que les cases jouables (jamais les cases noires).
            for r in range(board.rows):
                for c in range(board.cols):
                    if board.is_white(r, c) and saved[r][c]:
                        board.letters[r][c] = saved[r][c]
        found_ids = set(entry.get("found", []))
        for w in board.words:
            if w.id in found_ids:
                w.found = True

    def _save_progress(self) -> None:
        """Enregistre la progression de la grille courante (indexée par numéro)."""
        board, puzzle = self.board, self.puzzle
        if not board or not puzzle:
            return
        data = _read_saves()
        data[str(puzzle["number"])] = {
            "number": puzzle["number"],
            "date": puzzle["date"],
            "display": puzzle.get("display"),
            "letters": board.letters,
            "found": [w.id for w in board.words if w.found],
            "total": len(board.words),
        }
        _write_saves(data)

    async def _build_clues(self) -> None:
        """(Re)crée une définition cliquable par mot, dans l'ordre de la grille."""
        self.clue_items.clear()
        for axe, list_id in (("x", "#across-list"), ("y", "#down-list")):
            container = self.query_one(list_id, Vertical)
            await container.remove_children()  # vide l'ancienne grille si on change
            words = sorted(
                (w for w in self.board.words if w.axe == axe),
                key=lambda w: w.cells[0],
            )
            items = []
            for w in words:
                item = ClueItem(w, classes="clue-item")
                self.clue_items[w.id] = item
                items.append(item)
            await container.mount_all(items)

    # ---------- Rendu ----------

    def _render_all(self) -> None:
        self._render_grid()
        self._render_current_clue()
        self._render_clues()

    def _render_current_clue(self) -> None:
        board = self.board
        if not board:
            return
        w = board.word_through(*self.cursor, self.direction)
        widget = self.query_one("#current-clue", Static)
        if not w:
            widget.update("")
            return
        sens = "Horizontalement" if w.axe == "x" else "Verticalement"
        widget.update(f"[bold $accent]{sens} {w.label}.[/] {escape(w.definition)}")

    def _render_grid(self) -> None:
        board = self.board
        if not board:
            return
        cols, rows = board.cols, board.rows
        cur_word = board.word_through(*self.cursor, self.direction)
        word_cells = set(cur_word.cells) if cur_word else set()
        dim = "#44475a"
        dash = "─" * CELL_W

        def hline(left: str, mid: str, right: str) -> str:
            body = (dash + mid) * (cols - 1) + dash
            return f"[{dim}]{PAD}{left}{body}{right}[/]"

        # En-tête : numéros de colonnes alignés sur le centre des cases.
        header = [" "] * (LABEL_W + 1 + STRIDE_X * cols)
        for c in range(cols):
            header[_cell_center_x(c)] = str(c + 1)
        lines = ["".join(header), hline("┌", "┬", "┐")]

        bar = f"[{dim}]│[/]"
        for r in range(rows):
            parts = [f"[#bd93f9]{chr(65 + r)}[/] "]
            for c in range(cols):
                parts.append(bar)
                if not board.is_white(r, c):
                    # Case noire (bloquée, non cliquable) : vraiment noire.
                    parts.append(f"[on #0a0a0f]{' ' * CELL_W}[/]")
                    continue
                ch = board.letters[r][c] or " "
                cell = f"{ch:^{CELL_W}}"
                if board.cell_locked(r, c):
                    parts.append(f"[bold #14401f on #50fa7b]{cell}[/]")  # mot trouvé (vert)
                elif (r, c) == self.cursor:
                    parts.append(f"[bold #21222c on #ff79c6]{cell}[/]")  # curseur (rose)
                elif board.cell_wrong(r, c):
                    parts.append(f"[bold #5a2d00 on #ffb86c]{cell}[/]")  # mot faux (orange)
                elif (r, c) in word_cells:
                    parts.append(f"[#21222c on #8be9fd]{cell}[/]")       # mot courant (cyan)
                else:
                    parts.append(f"[#21222c on #e9e9f2]{cell}[/]")       # case vide (blanche)
            parts.append(bar)
            lines.append("".join(parts))
            lines.append(hline("├", "┼", "┤") if r < rows - 1 else hline("└", "┴", "┘"))
        self.query_one("#grid-view", GridView).update("\n".join(lines))

    def _render_clues(self) -> None:
        board = self.board
        if not board:
            return
        cur_word = board.word_through(*self.cursor, self.direction)
        cur_id = cur_word.id if cur_word else None

        for wid, item in self.clue_items.items():
            w = board.by_id[wid]
            mark = "✓ " if w.found else ("✗ " if w.wrong else "  ")
            item.update(f"{mark}[bold]{w.label}.[/] {escape(w.definition)}")
            item.set_class(w.found, "found")
            item.set_class(w.wrong and not w.found, "wrong")
            item.set_class(wid == cur_id, "current")

    # ---------- Interactions ----------

    def on_grid_click(self, x: int, y: int) -> None:
        board = self.board
        if not board:
            return
        # Inverse de _cell_center_x / _cell_center_y (cases avec bordures).
        r = round((y - 2) / 2)
        c = round((x - (LABEL_W + 2)) / STRIDE_X)
        if not (0 <= r < board.rows and 0 <= c < board.cols):
            return
        if not board.is_white(r, c):
            return

        cw = board.cell_words[(r, c)]
        if (r, c) == self._last_click and cw["x"] and cw["y"]:
            # Re-clic sur la même case : on bascule sur le mot vertical.
            self.direction = "y"
            self._last_click = None  # un clic suivant reviendra à l'horizontale
        else:
            self.cursor = (r, c)
            # Premier clic : on sélectionne le mot horizontal s'il existe.
            self.direction = "x" if cw["x"] else "y"
            self._last_click = (r, c)
        self._render_all()

    def select_word(self, word: Word) -> None:
        """Sélectionne un mot (clic sur sa définition) sur sa 1re case libre."""
        board = self.board
        if not board:
            return
        target = next(
            ((r, c) for (r, c) in word.cells if not board.cell_locked(r, c)),
            word.cells[0],
        )
        self.cursor = target
        self.direction = word.axe
        self._last_click = None
        self._render_all()

    def on_key(self, event) -> None:
        if self._modal_open:
            return  # palette / sélecteur ouvert : on ne saisit pas dans la grille
        ch = event.character
        if ch and len(ch) == 1 and ch.isalpha():
            event.stop()
            self._input_letter(ch.upper())

    def _input_letter(self, letter: str) -> None:
        board = self.board
        if not board:
            return
        self._last_click = None
        word = board.word_through(*self.cursor, self.direction)
        if not word:
            return

        # Position du curseur dans le mot, puis première case non verrouillée.
        try:
            pos = word.cells.index(self.cursor)
        except ValueError:
            pos = 0
        while pos < len(word.cells) and board.cell_locked(*word.cells[pos]):
            pos += 1
        if pos >= len(word.cells):
            return  # mot entièrement verrouillé

        r, c = word.cells[pos]
        board.letters[r][c] = letter
        # Modifier une case efface l'état « erroné » de ses mots.
        board.clear_wrong(r, c)

        # Avance jusqu'à la prochaine case non verrouillée du mot.
        nxt = pos + 1
        while nxt < len(word.cells) and board.cell_locked(*word.cells[nxt]):
            nxt += 1
        self.cursor = word.cells[nxt] if nxt < len(word.cells) else (r, c)

        self._render_all()
        self._save_progress()
        self._validate()

    def action_move(self, dr: int, dc: int) -> None:
        board = self.board
        if not board:
            return
        self._last_click = None
        self.direction = "x" if dc != 0 else "y"
        r, c = self.cursor
        nr, nc = r + dr, c + dc
        while 0 <= nr < board.rows and 0 <= nc < board.cols:
            if board.is_white(nr, nc):
                self.cursor = (nr, nc)
                break
            nr, nc = nr + dr, nc + dc
        self._render_all()

    def action_toggle_direction(self) -> None:
        if not self.board:
            return
        cw = self.board.cell_words.get(self.cursor)
        if cw and cw["x"] and cw["y"]:
            self.direction = "y" if self.direction == "x" else "x"
            self._render_all()

    def action_backspace(self) -> None:
        board = self.board
        if not board:
            return
        word = board.word_through(*self.cursor, self.direction)
        if not word:
            return
        r, c = self.cursor
        if board.letters[r][c] and not board.cell_locked(r, c):
            board.letters[r][c] = ""
            board.clear_wrong(r, c)
        else:
            # Recule vers la case précédente non verrouillée et l'efface.
            try:
                pos = word.cells.index(self.cursor)
            except ValueError:
                pos = 0
            pos -= 1
            while pos >= 0 and board.cell_locked(*word.cells[pos]):
                pos -= 1
            if pos >= 0:
                self.cursor = word.cells[pos]
                board.letters[self.cursor[0]][self.cursor[1]] = ""
                board.clear_wrong(*self.cursor)
        self._render_all()
        self._save_progress()

    def action_next_word(self) -> None:
        board = self.board
        if not board:
            return
        cur = board.word_through(*self.cursor, self.direction)
        order = board.words
        start = order.index(cur) if cur in order else -1
        for i in range(1, len(order) + 1):
            w = order[(start + i) % len(order)]
            if not w.found:
                self.cursor = w.cells[0]
                self.direction = w.axe
                break
        self._render_all()

    # ---------- Validation (côté serveur) ----------

    @work(exclusive=True, group="check")
    async def _validate(self) -> None:
        board, puzzle = self.board, self.puzzle
        if not board or not puzzle:
            return
        filled = board.filled_words()
        # On ne déclenche une vérification que pour un mot fraîchement complété
        # (ni déjà trouvé, ni déjà marqué erroné).
        pending = {
            w["id"]
            for w in filled
            if not board.by_id[w["id"]].found and not board.by_id[w["id"]].wrong
        }
        if not pending:
            return

        try:
            result = await check_words(self.client, puzzle, filled)
        except Exception:
            return

        # Réponse de complétion : grille entièrement remplie et correcte.
        # Le serveur ne renvoie alors pas de liste 'validated' mais 'code: true'.
        validated = result.get("validated")
        if validated is None:
            if result.get("code"):
                for w in board.words:
                    if not w.found and all(
                        board.letters[r][c] for (r, c) in w.cells
                    ):
                        w.found = True
                        w.wrong = False
                self._render_all()
                self._save_progress()
                self.notify(
                    "🎉 Bravo ! Grille terminée !", severity="information", timeout=10
                )
            return

        validated_ids = {str(item["id"]) for item in validated}

        changed = False
        for item in validated:
            w = board.by_id.get(str(item["id"]))
            if w and not w.found:
                w.found = True
                w.wrong = False
                changed = True
                # Fige les lettres correctes renvoyées par le serveur.
                for (r, c), ltr in zip(w.cells, item["word"]):
                    board.letters[r][c] = ltr
                self.notify(f"Mot trouvé : {item['word']}", severity="information")

        # Mots complétés mais incorrects -> orange (jusqu'à modification).
        for wid in pending:
            w = board.by_id[wid]
            if not w.found and wid not in validated_ids and not w.wrong:
                w.wrong = True
                changed = True

        if changed:
            self._render_all()
            self._save_progress()
            if board.all_found():
                self.notify(
                    "🎉 Bravo ! Grille terminée !", severity="information", timeout=10
                )


if __name__ == "__main__":
    MotsCroisesTUI().run()
