# This backend knows which games Nvidia's GeForce NOW carries (from Nvidia's public catalogue,
# read once a day), whether Nvidia's own app is on the Deck, and which games the player chose to
# have as Steam entries. It never sees the player's Nvidia account: signing in happens in
# Nvidia's app, which this add-on only opens.
import asyncio
import base64
import json
import os
import re
import ssl
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.request

import decky

# Nvidia's public list of games: no sign-in, and nothing about the player goes in the question.
CATALOGUE_URL = "https://api-prod.nvidia.com/services/gfngames/v1/gameList"
COUNTRY = "US"
LANGUAGE = "en_US"
PAGE = 700  # games asked for at a time (Nvidia takes 750 at most)
MAX_PAGES = 20  # the whole list is five or six pages today
MAX_BYTES = 8 * 1024 * 1024  # per answer
TIMEOUT = 15
DAY = 24 * 60 * 60
RETRY = 10 * 60  # after a read that failed, the next unasked try waits this long
# Nvidia's app for the Deck, as flatpak knows it.
NVIDIA_APP = "com.nvidia.geforcenow"
APP_CHECK = 60  # seconds an "is the app installed" answer is believed

# Decky's Python brings no certificates of its own: the system's are used to check Nvidia's.
CA_FILES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/ca-certificates/extracted/tls-ca-bundle.pem", "/etc/pki/tls/certs/ca-bundle.crt")

RUNTIME = decky.DECKY_PLUGIN_RUNTIME_DIR
CATALOGUE_FILE = os.path.join(RUNTIME, "catalogue.json")
# The games the player chose to have in Steam, and the last one started from a Steam game's page.
CHOSEN_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "chosen.json")
# Pictures come from Nvidia's picture server and nowhere else.
ART_BASE = "https://img.nvidiagrid.net/"
ART_BYTES = 6 * 1024 * 1024
ART_KINDS = {"box": 2, "hero": 3}  # where each picture's address sits in a game's row
MAX_TITLE = 120
MAX_FOUND = 20
# The launcher Steam starts, and beside it the one game it may open.
LAUNCHER_SOURCE = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "gfn-launch")
LAUNCHER = os.path.join(RUNTIME, "gfn-launch")
LAUNCH_FILE = os.path.join(RUNTIME, "launch.json")
# What the catalogue says of each chosen game (its genres and a few lines about it), for its page.
ABOUT_FILE = os.path.join(RUNTIME, "about.json")
ABOUT_AGE = 7 * DAY  # asked again after this long
LANGUAGES = {"es": "es_ES", "en": "en_US"}  # the two the page is written in
MAX_SUMMARY = 1500
MAX_GENRES = 6

_CURSOR = re.compile(r"[A-Za-z0-9+/=_-]{1,200}")
_ART_PATH = re.compile(r"[A-Za-z0-9/._~%-]{1,400}")
_STORE = re.compile(r"[A-Z0-9_]{1,24}")
_GENRE = re.compile(r"[A-Z0-9_]{1,40}")

_lock = threading.Lock()  # guards the values below
_reading = threading.Lock()  # held while the catalogue is being read from Nvidia
# {"read": seconds, "games": {steam number: nvidia number},
#  "rows": {nvidia number: [title, shop, cover address, wide picture address]}}; None: not loaded yet
_catalogue = None
_folded = (None, {})  # (the rows these were made from, {nvidia number: title as it is searched})
_chosen_lock = threading.Lock()  # held while the chosen games' file is read and written
_tried = None  # when the last read from Nvidia started (monotonic)
_app = (None, False)  # (when it was checked, whether Nvidia's app is installed)
_about_lock = threading.Lock()  # held while the chosen games' facts are read and written
_about_tried = {}  # {"number:language": when asking Nvidia about it last failed (monotonic)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The catalogue has one address: a redirect is refused instead of followed somewhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener(ctx):
    return urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ctx))


def _write_private(path, data):
    """True when the file is safely on disk, for this user only; a half-written copy never stays."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except OSError as e:
        decky.logger.error("%s not saved: %s", os.path.basename(path), e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _digits(v):
    """A number written with plain digits, as text; None for anything else."""
    return v if isinstance(v, str) and v.isascii() and v.isdigit() and len(v) <= 12 and int(v) > 0 else None


def _number(v):
    """A positive whole number from what the frontend sent, or None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = int(v) if _digits(v) else None
    return v if isinstance(v, int) and 0 < v < 2**32 else None


def _query(after):
    """The question for one page of the catalogue: each game's name, pictures and editions."""
    page = 'first:%d after:"%s"' % (PAGE, after) if after else "first:%d" % PAGE
    return ('{apps(country:"%s" language:"%s" %s){pageInfo{endCursor hasNextPage totalCount} '
            "items{title images{GAME_BOX_ART HERO_IMAGE KEY_ART TV_BANNER} variants{appStore storeId id}}}}"
            % (COUNTRY, LANGUAGE, page))


def _page(after):
    """One page of the catalogue as Nvidia answers it, or None when it did not come whole."""
    return _ask(_query(after))


def _ask(question):
    """Nvidia's answer to one question about its catalogue, or None when it did not come whole."""
    req = urllib.request.Request(CATALOGUE_URL, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Vistazo")
    ctx = None
    if CATALOGUE_URL.startswith("https:"):
        ctx = ssl.create_default_context(cafile=next((f for f in CA_FILES if os.path.isfile(f)), None))
    try:
        with _opener(ctx).open(req, data=question.encode("utf-8"), timeout=TIMEOUT) as r:
            raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            decky.logger.warning("Catalogue: an answer was too long")
            return None
        apps = json.loads(raw)["data"]["apps"]
        return apps if isinstance(apps, dict) else None
    except urllib.error.HTTPError as e:
        decky.logger.warning("Catalogue: answered %s", e.code)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
        decky.logger.warning("Catalogue not read: %s", getattr(e, "reason", e))
    return None


def _art_url(v):
    """A picture's address when it is on Nvidia's picture server, written plainly; else ""."""
    if isinstance(v, str) and v.startswith(ART_BASE) and _ART_PATH.fullmatch(v[len(ART_BASE):]):
        return v
    return ""


def _title(v):
    """A game's name without odd characters and of a sane length; "" when there is none."""
    if not isinstance(v, str):
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in v).split())[:MAX_TITLE].strip()


def _text(v, limit):
    """A few lines of text without odd characters, cut at a word; "" when there is none."""
    if not isinstance(v, str):
        return ""
    text = " ".join("".join(c if c.isprintable() else " " for c in v).split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"
    return text


def _row(v):
    """A game's row as it is kept, when every part of it is what it should be; else None."""
    if not isinstance(v, list) or len(v) != 4 or not all(isinstance(x, str) for x in v):
        return None
    title, store = _title(v[0]), v[1]
    if not title or not _STORE.fullmatch(store):
        return None
    return [title, store, _art_url(v[2]), _art_url(v[3])]


def _editions(items, games, rows):
    """Adds one page's games: to `rows` every edition, to `games` the Steam ones by Steam's number."""
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        images = item.get("images") if isinstance(item.get("images"), dict) else {}
        box = _art_url(images.get("GAME_BOX_ART"))
        hero = next(filter(None, (_art_url(images.get(k)) for k in ("HERO_IMAGE", "KEY_ART", "TV_BANNER"))), "")
        variants = item.get("variants")
        for variant in variants if isinstance(variants, list) else ():
            if not isinstance(variant, dict):
                continue
            nvidia = _digits(variant.get("id"))
            if not nvidia:
                continue
            row = _row([item.get("title"), variant.get("appStore"), box, hero])
            if row:
                rows.setdefault(nvidia, row)
            steam = _digits(variant.get("storeId"))
            if variant.get("appStore") == "STEAM" and steam:
                games.setdefault(steam, nvidia)


def _read_catalogue():
    """({Steam number: Nvidia's number}, {Nvidia's number: row}) for the whole catalogue, or None
    when any page failed: half a catalogue would hide games that are there."""
    games, rows, after = {}, {}, ""
    for _ in range(MAX_PAGES):
        apps = _page(after)
        if apps is None:
            return None
        _editions(apps.get("items"), games, rows)
        info = apps.get("pageInfo")
        if not isinstance(info, dict) or info.get("hasNextPage") is not True:
            break
        after = info.get("endCursor")
        # The cursor goes back inside the question: only one that cannot change it is used.
        if not isinstance(after, str) or not _CURSOR.fullmatch(after):
            return None
    # Past the last allowed page what was read is kept: more than that is not a real catalogue.
    return (games, rows) if games else None


def _load_catalogue():
    try:
        with open(CATALOGUE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        read, games, rows = data["read"], data["games"], data.get("rows")
        if isinstance(read, (int, float)) and not isinstance(read, bool) and isinstance(games, dict):
            games = {k: v for k, v in games.items() if _digits(k) and _digits(v)}
            if not isinstance(rows, dict):
                # A copy from before names were kept: good for Steam pages, and due to be read again.
                return {"read": None, "games": games, "rows": {}}
            rows = {k: _row(v) for k, v in rows.items() if _digits(k)}
            return {"read": read, "games": games, "rows": {k: v for k, v in rows.items() if v}}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {"read": None, "games": {}, "rows": {}}


def _current():
    """The catalogue as last read, from memory or from its file."""
    global _catalogue
    with _lock:
        if _catalogue is None:
            _catalogue = _load_catalogue()
        return _catalogue


def _age():
    """Seconds since the catalogue was read; None when it never was."""
    read = _current()["read"]
    return None if read is None else max(0, int(time.time() - read))


def _refresh(wait):
    """Reads the catalogue from Nvidia and keeps it. False when it failed (the last good copy
    stays) or, without `wait`, when a read was already under way."""
    global _catalogue, _tried
    if not _reading.acquire(blocking=wait):
        return False
    try:
        with _lock:
            _tried = time.monotonic()
        read = _read_catalogue()
        if not read:
            return False
        fresh = {"read": int(time.time()), "games": read[0], "rows": read[1]}
        with _lock:
            _catalogue = fresh
        _write_private(CATALOGUE_FILE, fresh)
        return True
    finally:
        _reading.release()


def _refresh_if_old():
    """Starts a read on the side when the copy is older than a day. Whoever asked does not wait:
    it gets the copy there is."""
    age = _age()
    with _lock:
        waited = _tried is None or time.monotonic() - _tried >= RETRY
    if (age is None or age > DAY) and waited and not _reading.locked():
        threading.Thread(target=_refresh, args=(False,), daemon=True).start()


def _app_installed():
    """Whether Nvidia's app is on the Deck, asked to flatpak at most once a minute."""
    global _app
    with _lock:
        checked, there = _app
    if checked is not None and time.monotonic() - checked < APP_CHECK:
        return there
    # Decky runs its Python with library paths of its own, which break system programs.
    env = {k: v for k, v in os.environ.items() if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")}
    try:
        done = subprocess.run(["flatpak", "info", NVIDIA_APP], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10, env=env)
        there = done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        there = False
    with _lock:
        _app = (time.monotonic(), there)
    return there


def _install_launcher():
    """The launcher beside its list, in the plugin's own data folder: the plugin's folder is
    replaced on every update, and its files may not be runnable."""
    try:
        with open(LAUNCHER_SOURCE, "rb") as f:
            wanted = f.read()
        try:
            with open(LAUNCHER, "rb") as f:
                same = f.read() == wanted and os.stat(LAUNCHER).st_mode & 0o777 == 0o700
        except OSError:
            same = False
        if not same:
            os.makedirs(RUNTIME, exist_ok=True)
            tmp = LAUNCHER + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
            with os.fdopen(fd, "wb") as f:
                f.write(wanted)
            os.chmod(tmp, 0o700)
            os.replace(tmp, LAUNCHER)
        return True
    except OSError as e:
        decky.logger.error("Launcher not installed: %s", e)
        return False


def _fold(text):
    """A name as it is compared: no accents, no capitals, no signs."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in plain).split())


def _search(text):
    """The catalogue's games whose name holds `text`: those that start with it first, and of one
    game's editions Steam's first."""
    global _folded
    wanted = _fold(text)
    if not wanted:
        return []
    rows = _current()["rows"]
    with _lock:
        made_from, names = _folded
    if made_from is not rows:
        names = {nvidia: _fold(row[0]) for nvidia, row in rows.items()}
        with _lock:
            _folded = (rows, names)
    found = [nvidia for nvidia, name in names.items() if wanted in name]
    found.sort(key=lambda n: (not names[n].startswith(wanted), names[n], rows[n][1] != "STEAM", rows[n][1], int(n)))
    return [{"id": n, "title": rows[n][0], "store": rows[n][1]} for n in found[:MAX_FOUND]]


def _read_chosen():
    """{"games": {Nvidia's number: {title, store, appid}}, "last": Nvidia's number or None}."""
    data = {"games": {}, "last": None}
    try:
        with open(CHOSEN_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        for nvidia, game in raw["games"].items():
            row = _row([game.get("title"), game.get("store"), "", ""]) if isinstance(game, dict) else None
            appid = _number(game.get("appid")) if row else None
            if _digits(nvidia) and row and appid:
                data["games"][nvidia] = {"title": row[0], "store": row[1], "appid": appid}
        data["last"] = _digits(raw.get("last"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return data


def _write_chosen(data):
    """Keeps the chosen games and tells the launcher which games it may open: every chosen one
    and the last one started from a Steam game's page."""
    allowed = {nvidia: True for nvidia in data["games"]}
    if data["last"]:
        allowed[data["last"]] = True
    return _write_private(CHOSEN_FILE, data) and _write_private(LAUNCH_FILE, allowed)


def _about_query(nvidia, language):
    """The question for one game, by Nvidia's number: its genres and a few lines about it."""
    return ('{apps(country:"%s" language:"%s" first:1 filters:{variants:{id:{in:[%s]}}}){'
            "items{genres shortDescription variants{id}}}}" % (COUNTRY, language, nvidia))


def _fetch_about(nvidia, language):
    """{"genres", "summary"} for one game as Nvidia tells it now, or None when it did not answer.
    A game the catalogue no longer has comes back empty."""
    apps = _ask(_about_query(nvidia, language))
    if apps is None:
        return None
    items = apps.get("items")
    for item in items if isinstance(items, list) else ():
        variants = item.get("variants") if isinstance(item, dict) else None
        # Only the game that was asked for: an answer about another one is not believed.
        if not any(isinstance(v, dict) and v.get("id") == nvidia for v in (variants if isinstance(variants, list) else ())):
            continue
        genres = item.get("genres")
        genres = [g for g in (genres if isinstance(genres, list) else ()) if isinstance(g, str) and _GENRE.fullmatch(g)]
        return {"genres": genres[:MAX_GENRES], "summary": _text(item.get("shortDescription"), MAX_SUMMARY)}
    return {"genres": [], "summary": ""}


def _read_about():
    """{"number:language": {"read": seconds, "genres": [...], "summary": text}} as last kept."""
    out = {}
    try:
        with open(ABOUT_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        for key, known in raw.items():
            nvidia, _, language = key.partition(":")
            read = known.get("read") if isinstance(known, dict) else None
            if not _digits(nvidia) or language not in LANGUAGES or isinstance(read, bool) or not isinstance(read, (int, float)):
                continue
            genres = known.get("genres")
            out[key] = {
                "read": read,
                "genres": [g for g in (genres if isinstance(genres, list) else ()) if isinstance(g, str) and _GENRE.fullmatch(g)][:MAX_GENRES],
                "summary": _text(known.get("summary"), MAX_SUMMARY),
            }
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return out


def _about(nvidia, language, chosen):
    """What is known of one chosen game, asked to Nvidia when it is new or a week old. None when
    nothing is known and Nvidia did not answer. Only the chosen games' facts stay on disk."""
    key = "%s:%s" % (nvidia, language)
    with _about_lock:
        kept = _read_about()
        known = kept.get(key)
        fresh = known is not None and 0 <= time.time() - known["read"] < ABOUT_AGE
        tried = _about_tried.get(key)
        if fresh or (tried is not None and time.monotonic() - tried < RETRY):
            return known
        got = _fetch_about(nvidia, LANGUAGES[language])
        if got is None:
            _about_tried[key] = time.monotonic()
            return known
        _about_tried.pop(key, None)
        kept[key] = {"read": int(time.time()), **got}
        _write_private(ABOUT_FILE, {k: v for k, v in kept.items() if k.partition(":")[0] in chosen})
        return kept[key]


def _forget_about(nvidia):
    """Drops what was kept of a game the player took out."""
    with _about_lock:
        kept = _read_about()
        left = {k: v for k, v in kept.items() if k.partition(":")[0] != nvidia}
        if len(left) != len(kept):
            _write_private(ABOUT_FILE, left)


def _fetch_art(url):
    """A picture from Nvidia's picture server as (bytes, "jpg" or "png"), or None."""
    if not _art_url(url):
        return None
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "Vistazo")
    ctx = None
    if url.startswith("https:"):
        ctx = ssl.create_default_context(cafile=next((f for f in CA_FILES if os.path.isfile(f)), None))
    try:
        with _opener(ctx).open(req, timeout=TIMEOUT) as r:
            raw = r.read(ART_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        decky.logger.warning("Picture not read: %s", getattr(e, "code", None) or getattr(e, "reason", e))
        return None
    if len(raw) > ART_BYTES:
        return None
    # Only what really is a picture is handed to Steam.
    if raw.startswith(b"\xff\xd8\xff"):
        return raw, "jpg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return raw, "png"
    return None


def _status(app):
    return {"app": app, "games": len(_current()["games"]), "age": _age(), "refreshing": _reading.locked()}


class Plugin:
    async def status(self):
        """What the settings show: whether Nvidia's app is there and how the catalogue stands."""
        _refresh_if_old()
        return _status(await asyncio.to_thread(_app_installed))

    async def game(self, appid):
        """Nvidia's number for a Steam game, when GeForce NOW carries it."""
        appid = _number(appid)
        if appid is None:
            return {"ok": False}
        _refresh_if_old()
        nvidia = _current()["games"].get(str(appid))
        return {"ok": True, "id": nvidia} if nvidia else {"ok": False}

    async def refresh(self):
        """Reads the catalogue again now, when the player asks for it."""
        ok = await asyncio.to_thread(_refresh, True)
        return {"ok": ok, **_status(await asyncio.to_thread(_app_installed))}

    async def prepare(self, appid):
        """Gets a game ready to start: the launcher is in place and allowed to open that one game.
        Returns what Steam's entry needs; all Steam is told about the game is Nvidia's number."""
        appid = _number(appid)
        nvidia = _current()["games"].get(str(appid)) if appid is not None else None
        if not nvidia:
            return {"ok": False, "why": "game"}
        if not await asyncio.to_thread(_app_installed):
            return {"ok": False, "why": "app"}
        with _chosen_lock:
            data = _read_chosen()
            data["last"] = nvidia
            if not _install_launcher() or not _write_chosen(data):
                return {"ok": False, "why": "error"}
        return {"ok": True, "exe": LAUNCHER, "start_dir": RUNTIME, "options": nvidia}

    async def search(self, text):
        """Games of the catalogue by name, for the player to choose one. `text` is only ever
        compared with the names in memory."""
        if not isinstance(text, str) or not 2 <= len(text.strip()) <= 60:
            return []
        _refresh_if_old()
        return _search(text.strip())

    async def entry(self):
        """Where the launcher is, for a new Steam entry."""
        if not _install_launcher():
            return {"ok": False}
        return {"ok": True, "exe": LAUNCHER, "start_dir": RUNTIME}

    async def chosen(self):
        """The games the player chose, by name."""
        with _chosen_lock:
            games = _read_chosen()["games"]
        return sorted(({"id": n, **g} for n, g in games.items()), key=lambda g: (_fold(g["title"]), int(g["id"])))

    async def choose(self, id, appid):
        """Remembers a game the player chose and the Steam entry the frontend made for it."""
        nvidia, appid = _digits(str(id)) if _number(id) else None, _number(appid)
        row = _current()["rows"].get(nvidia) if nvidia else None
        if not row or appid is None:
            return {"ok": False}
        with _chosen_lock:
            data = _read_chosen()
            if nvidia in data["games"]:
                return {"ok": False}
            data["games"][nvidia] = {"title": row[0], "store": row[1], "appid": appid}
            return {"ok": _install_launcher() and _write_chosen(data)}

    async def unchoose(self, id):
        """Forgets a chosen game: the launcher will not open it any more."""
        nvidia = _digits(str(id)) if _number(id) else None
        with _chosen_lock:
            data = _read_chosen()
            if not nvidia or nvidia not in data["games"]:
                return {"ok": False}
            del data["games"][nvidia]
            ok = _write_chosen(data)
        _forget_about(nvidia)
        return {"ok": ok}

    async def art(self, id, kind):
        """One of a game's pictures, for its Steam entry: the cover ("box") or the wide one ("hero")."""
        nvidia = _digits(str(id)) if _number(id) else None
        row = _current()["rows"].get(nvidia) if nvidia else None
        if not row or not isinstance(kind, str) or kind not in ART_KINDS:
            return {"ok": False}
        got = await asyncio.to_thread(_fetch_art, row[ART_KINDS[kind]])
        if not got:
            return {"ok": False}
        return {"ok": True, "data": base64.b64encode(got[0]).decode("ascii"), "kind": got[1]}

    async def about(self, id, language):
        """What the catalogue says of a game the player chose, for its page: the shop, the genres
        and a few lines about it, in the page's language."""
        nvidia = _digits(str(id)) if _number(id) else None
        if not nvidia or not isinstance(language, str) or language not in LANGUAGES:
            return {"ok": False}
        with _chosen_lock:
            games = _read_chosen()["games"]
        if nvidia not in games:
            return {"ok": False}
        known = await asyncio.to_thread(_about, nvidia, language, set(games))
        return {"ok": True, "store": games[nvidia]["store"], "genres": known["genres"] if known else [],
                "summary": known["summary"] if known else ""}

    async def _main(self):
        _install_launcher()
        with _chosen_lock:
            data = _read_chosen()
            if data["games"] or data["last"]:
                _write_chosen(data)
        _refresh_if_old()

    async def _unload(self):
        pass
