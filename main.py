# This backend knows two things: which Steam games Nvidia's GeForce NOW carries (from Nvidia's
# public catalogue, read once a day) and whether Nvidia's own app is on the Deck. It never sees
# the player's Nvidia account: signing in happens in Nvidia's app, which this add-on only opens.
import asyncio
import json
import os
import re
import ssl
import subprocess
import threading
import time
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
# The launcher Steam starts, and beside it the one game it may open.
LAUNCHER_SOURCE = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "gfn-launch")
LAUNCHER = os.path.join(RUNTIME, "gfn-launch")
LAUNCH_FILE = os.path.join(RUNTIME, "launch.json")

_CURSOR = re.compile(r"[A-Za-z0-9+/=_-]{1,200}")

_lock = threading.Lock()  # guards the values below
_reading = threading.Lock()  # held while the catalogue is being read from Nvidia
_catalogue = None  # {"read": seconds, "games": {steam number: nvidia number}}; None: not loaded yet
_tried = None  # when the last read from Nvidia started (monotonic)
_app = (None, False)  # (when it was checked, whether Nvidia's app is installed)


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
    """The question for one page of the catalogue: only what links a Steam game to Nvidia's."""
    page = 'first:%d after:"%s"' % (PAGE, after) if after else "first:%d" % PAGE
    return ('{apps(country:"%s" language:"%s" %s){pageInfo{endCursor hasNextPage totalCount} '
            "items{variants{appStore storeId id}}}}" % (COUNTRY, LANGUAGE, page))


def _page(after):
    """One page of the catalogue as Nvidia answers it, or None when it did not come whole."""
    req = urllib.request.Request(CATALOGUE_URL, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Vistazo")
    ctx = None
    if CATALOGUE_URL.startswith("https:"):
        ctx = ssl.create_default_context(cafile=next((f for f in CA_FILES if os.path.isfile(f)), None))
    try:
        with _opener(ctx).open(req, data=_query(after).encode("utf-8"), timeout=TIMEOUT) as r:
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


def _steam_games(items, games):
    """Adds to `games` the Steam editions among one page's games."""
    for item in items if isinstance(items, list) else ():
        variants = item.get("variants") if isinstance(item, dict) else None
        for variant in variants if isinstance(variants, list) else ():
            if not isinstance(variant, dict) or variant.get("appStore") != "STEAM":
                continue
            steam, nvidia = _digits(variant.get("storeId")), _digits(variant.get("id"))
            if steam and nvidia:
                games.setdefault(steam, nvidia)


def _read_catalogue():
    """{Steam number: Nvidia's number} for the whole catalogue, or None when any page failed:
    half a catalogue would hide games that are there."""
    games, after = {}, ""
    for _ in range(MAX_PAGES):
        apps = _page(after)
        if apps is None:
            return None
        _steam_games(apps.get("items"), games)
        info = apps.get("pageInfo")
        if not isinstance(info, dict) or info.get("hasNextPage") is not True:
            break
        after = info.get("endCursor")
        # The cursor goes back inside the question: only one that cannot change it is used.
        if not isinstance(after, str) or not _CURSOR.fullmatch(after):
            return None
    # Past the last allowed page what was read is kept: more than that is not a real catalogue.
    return games or None


def _load_catalogue():
    try:
        with open(CATALOGUE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        read, games = data["read"], data["games"]
        if isinstance(read, (int, float)) and not isinstance(read, bool) and isinstance(games, dict):
            return {"read": read, "games": {k: v for k, v in games.items() if _digits(k) and _digits(v)}}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {"read": None, "games": {}}


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
        games = _read_catalogue()
        if not games:
            return False
        fresh = {"read": int(time.time()), "games": games}
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
        if not _install_launcher() or not _write_private(LAUNCH_FILE, {nvidia: True}):
            return {"ok": False, "why": "error"}
        return {"ok": True, "exe": LAUNCHER, "start_dir": RUNTIME, "options": nvidia}

    async def _main(self):
        _install_launcher()
        _refresh_if_old()

    async def _unload(self):
        pass
