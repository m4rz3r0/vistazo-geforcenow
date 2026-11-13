#!/usr/bin/env python3
"""Backend checks against a pretend catalogue, all inside a throwaway folder: `python3 tools/test_backend.py`.

Nothing here talks to Nvidia or touches the real home: `decky` is a stand-in pointing at the
temporary folder, and "flatpak" is a script that only writes down what it was started with."""
import asyncio
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="vistazo-gfn-test-")
PLUGIN = os.path.join(TMP, "plugin")
FAKE_BIN = os.path.join(TMP, "bin")
SEEN = os.path.join(TMP, "flatpak-args.json")
APP_THERE = os.path.join(TMP, "app-installed")
os.makedirs(os.path.join(PLUGIN, "bin"))
os.makedirs(FAKE_BIN)
shutil.copy(os.path.join(REPO, "bin", "gfn-launch"), os.path.join(PLUGIN, "bin", "gfn-launch"))
# The pretend flatpak: "info" says whether the app is there; anything else is written down.
with open(os.path.join(FAKE_BIN, "flatpak"), "w") as f:
    f.write("#!/usr/bin/python3\nimport json, os, sys\n"
            "if sys.argv[1:2] == ['info']:\n    sys.exit(0 if os.path.exists(%r) else 1)\n"
            "json.dump(sys.argv[1:], open(%r, 'w'))\n" % (APP_THERE, SEEN))
os.chmod(os.path.join(FAKE_BIN, "flatpak"), 0o755)
os.environ["PATH"] = FAKE_BIN + ":/usr/bin:/bin"

decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_DIR = PLUGIN
decky.DECKY_HOME = os.path.join(TMP, "homebrew")
decky.DECKY_USER_HOME = os.path.join(TMP, "home")
decky.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(TMP, "settings")
decky.DECKY_PLUGIN_RUNTIME_DIR = os.path.join(TMP, "runtime")
decky.logger = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None)
sys.modules["decky"] = decky
sys.dont_write_bytecode = True
sys.path.insert(0, REPO)
import main  # noqa: E402


class Catalogue:
    """What the pretend server answers, and what it was asked."""

    def __init__(self):
        # Each page: a list of games. A game: a list of (shop, shop's number, Nvidia's number), or
        # {"title", "images", "variants": that list}.
        self.pages = []
        self.pictures = {}  # path -> bytes, or a (status, headers) answer
        self.asked = []  # (path, content type, question)
        self.down = False
        self.redirect_to = None
        self.padding = 0  # extra bytes in every answer
        self.endless = False  # every page says there is another
        self.cursor = None  # the cursor handed out instead of the usual one
        self.gate = None  # an Event the answer waits for


nvidia = Catalogue()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, status, body=None, headers=()):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        nvidia.asked.append((self.path, None, None))
        picture = nvidia.pictures.get(self.path)
        if isinstance(picture, tuple):
            return self.reply(picture[0], headers=picture[1])
        if picture is None:
            return self.reply(404)
        self.send_response(200)
        self.send_header("Content-Length", str(len(picture)))
        self.end_headers()
        self.wfile.write(picture)

    def do_POST(self):
        question = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        nvidia.asked.append((self.path, self.headers.get("Content-Type"), question))
        if nvidia.gate:
            nvidia.gate.wait(10)
        if nvidia.down:
            return self.reply(503)
        if nvidia.redirect_to:
            return self.reply(302, headers=[("Location", nvidia.redirect_to)])
        after = re.search(r'after:"([^"]*)"', question)
        index = int(after.group(1).removeprefix("page")) if after else 0
        games = nvidia.pages[index % len(nvidia.pages)] if nvidia.endless else nvidia.pages[index]
        more = nvidia.endless or index + 1 < len(nvidia.pages)
        self.reply(200, {"data": {"apps": {
            "pageInfo": {"endCursor": nvidia.cursor or "page%d" % (index + 1), "hasNextPage": more, "totalCount": 3},
            "items": [item(game) for game in games],
        }}, "padding": "x" * nvidia.padding})


def item(game):
    """A game as Nvidia writes it."""
    if isinstance(game, list):
        game = {"title": "Game %s" % game[0][2], "variants": game}
    return {**game, "variants": [{"appStore": s, "storeId": n, "id": i} for s, n, i in game["variants"]]}


class Elsewhere(BaseHTTPRequestHandler):
    """Another server: where a redirect would lead. Nothing may ever arrive here."""
    hits = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        Elsewhere.hits.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        raw = json.dumps({"data": {"apps": {"pageInfo": {"hasNextPage": False}, "items": [
            {"variants": [{"appStore": "STEAM", "storeId": "666", "id": "999"}]}]}}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
other = ThreadingHTTPServer(("127.0.0.1", 0), Elsewhere)
for s in (server, other):
    threading.Thread(target=s.serve_forever, daemon=True).start()
main.CATALOGUE_URL = "http://127.0.0.1:%d/services/gfngames/v1/gameList" % server.server_port
ART = "http://127.0.0.1:%d/" % server.server_port
main.ART_BASE = ART

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
plugin = main.Plugin()


def run(call):
    return asyncio.run_coroutine_threadsafe(call, loop).result(30)


checks = 0


def check(what, got, want=True):
    global checks
    checks += 1
    if got != want:
        raise AssertionError("%s: got %r, wanted %r" % (what, got, want))


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def settle(timeout=10):
    """Waits for a read started on the side to end."""
    end = time.monotonic() + timeout
    time.sleep(0.05)
    while main._reading.locked():
        if time.monotonic() > end:
            raise AssertionError("the read did not end")
        time.sleep(0.02)


def kept():
    with open(main.CATALOGUE_FILE) as f:
        return json.load(f)


def grow_old():
    """Makes the copy two days old, and forgets the last try."""
    with main._lock:
        main._catalogue["read"] -= 2 * main.DAY
        main._tried = None


PAGES = [
    [[("STEAM", "220", "19111"), ("EPIC", "abc", "19112")], [("EPIC", "220", "5")]],
    [[("STEAM", "400", "20000")], [("STEAM", "not-a-number", "3")], [("STEAM", "500", "x1")], [("STEAM", "-7", "4")],
     [("STEAM", "0", "6")], [("steam", "600", "7")], [("STEAM", 700, 8)], [("STEAM", "400", "20001")]],
    [[("UBISOFT", "1", "2")], [("STEAM", "620", "21000")]],
]
WANTED = {"220": "19111", "400": "20000", "620": "21000"}


def test_catalogue():
    # Before anything was read: nothing known, and asking does not wait for Nvidia.
    nvidia.down = True
    state = run(plugin.status())
    check("empty at first", (state["app"], state["games"], state["age"]), (False, 0, None))
    settle()
    check("no file from a failed read", os.path.exists(main.CATALOGUE_FILE), False)
    check("unknown while empty", run(plugin.game(220)), {"ok": False})
    nvidia.down = False

    # Read on demand: every page, in order, and only the Steam editions with plain numbers.
    nvidia.pages = PAGES
    nvidia.asked.clear()
    got = run(plugin.refresh())
    check("read", (got["ok"], got["games"], got["refreshing"]), (True, 3, False))
    check("age just read", got["age"] < 5)
    check("three pages asked", len(nvidia.asked), 3)
    check("one address", {a[0] for a in nvidia.asked}, {"/services/gfngames/v1/gameList"})
    check("content type", {a[1] for a in nvidia.asked}, {"application/json"})
    first = nvidia.asked[0][2]
    check("the question is the bare query", first.startswith('{apps(country:"US" language:"en_US" first:700)'))
    check("asks for names, pictures and editions, nothing else", first.endswith(
        "items{title images{GAME_BOX_ART HERO_IMAGE KEY_ART TV_BANNER} variants{appStore storeId id}}}}"))
    check("cursors followed", [re.findall(r'after:"([^"]*)"', a[2]) for a in nvidia.asked], [[], ["page1"], ["page2"]])
    check("kept on disk", kept()["games"], WANTED)
    check("file is private", mode(main.CATALOGUE_FILE), 0o600)
    check("no half-written copy", os.path.exists(main.CATALOGUE_FILE + ".tmp"), False)
    check("when it was read", abs(kept()["read"] - time.time()) < 5)

    # One game.
    check("known game", run(plugin.game(220)), {"ok": True, "id": "19111"})
    check("as text", run(plugin.game("400")), {"ok": True, "id": "20000"})
    check("another shop's number", run(plugin.game(1)), {"ok": False})
    for bad in (0, -220, 2.5, True, None, "220; rm -rf", "../220", "", [220], {"a": 1}, 2**40, "٢٢٠"):
        check("invalid %r" % (bad,), run(plugin.game(bad)), {"ok": False})

    # A fresh copy is not read again unasked.
    nvidia.asked.clear()
    run(plugin.status())
    run(plugin.game(220))
    settle()
    check("fresh: Nvidia not asked", nvidia.asked, [])

    # After a restart the copy comes from the file.
    main._catalogue = None
    check("from the file", run(plugin.game(620)), {"ok": True, "id": "21000"})
    check("still not asked", nvidia.asked, [])


def test_stale():
    # Older than a day: the old copy answers at once while the new one is read on the side.
    nvidia.pages = [[[("STEAM", "220", "19111")], [("STEAM", "730", "30000")]]]
    grow_old()
    nvidia.gate = threading.Event()
    started = time.monotonic()
    check("old copy meanwhile", run(plugin.game(620)), {"ok": True, "id": "21000"})
    check("not there yet", run(plugin.game(730)), {"ok": False})
    state = run(plugin.status())
    check("answered without waiting", time.monotonic() - started < 2)
    check("says it is reading", (state["refreshing"], state["games"]), (True, 3))
    check("old age shown", state["age"] > main.DAY)
    nvidia.gate.set()
    settle()
    nvidia.gate = None
    check("one read only", len(nvidia.asked), 1)
    check("new copy", run(plugin.game(730)), {"ok": True, "id": "30000"})
    check("gone from the catalogue", run(plugin.game(620)), {"ok": False})
    check("new copy on disk", kept()["games"], {"220": "19111", "730": "30000"})

    # Nvidia down: the last good copy stays, in memory and on disk, and it is not asked in a row.
    before = kept()
    grow_old()
    nvidia.down = True
    nvidia.asked.clear()
    check("stale copy used", run(plugin.game(730)), {"ok": True, "id": "30000"})
    settle()
    check("asked once", len(nvidia.asked), 1)
    run(plugin.game(730))
    run(plugin.status())
    settle()
    check("not asked again right away", len(nvidia.asked), 1)
    got = run(plugin.refresh())
    check("on demand it is asked, and says it failed", (len(nvidia.asked), got["ok"], got["games"]), (2, False, 2))
    check("disk untouched", kept(), before)
    nvidia.down = False

    # A redirect is not followed.
    nvidia.redirect_to = "http://127.0.0.1:%d/services/gfngames/v1/gameList" % other.server_port
    check("redirect refused", run(plugin.refresh())["ok"], False)
    check("nothing went elsewhere", Elsewhere.hits, [])
    check("nothing taken from elsewhere", run(plugin.game(666)), {"ok": False})
    check("disk untouched", kept(), before)
    nvidia.redirect_to = None


def test_caps():
    before = kept()
    # An answer longer than allowed is dropped whole.
    main.MAX_BYTES = 2000
    nvidia.padding = 5000
    check("too long", run(plugin.refresh())["ok"], False)
    check("disk untouched", kept(), before)
    nvidia.padding = 0
    check("short enough", run(plugin.refresh())["ok"], True)
    main.MAX_BYTES = 8 * 1024 * 1024

    # A catalogue that never ends stops at the last allowed page.
    nvidia.endless = True
    nvidia.asked.clear()
    check("endless still read", run(plugin.refresh())["ok"], True)
    check("stopped at the cap", len(nvidia.asked), main.MAX_PAGES)
    nvidia.endless = False

    # A cursor that would change the question is not sent back; a broken page keeps the old copy.
    nvidia.pages = PAGES
    nvidia.cursor = 'x"){evil'
    nvidia.asked.clear()
    check("odd cursor", run(plugin.refresh())["ok"], False)
    check("never sent back", len(nvidia.asked), 1)
    nvidia.cursor = None
    nvidia.pages = [[[("EPIC", "1", "2")]]]
    check("no Steam games at all", run(plugin.refresh())["ok"], False)
    check("old copy kept", run(plugin.game(730)), {"ok": True, "id": "30000"})
    nvidia.pages = PAGES
    check("read again", run(plugin.refresh())["games"], 3)

    # A file someone damaged is not believed.
    with open(main.CATALOGUE_FILE, "w") as f:
        json.dump({"read": time.time(), "games": {"220": "19111", "x": "1", "5": "$(id)", "6": 7}}, f)
    main._catalogue = None
    check("only plain numbers from the file", run(plugin.status())["games"], 1)
    with open(main.CATALOGUE_FILE, "w") as f:
        f.write("not json")
    main._catalogue = None
    with main._lock:
        main._tried = time.monotonic()
    check("broken file: empty", run(plugin.status())["games"], 0)
    check("read again", run(plugin.refresh())["games"], 3)


def launch(*args):
    if os.path.exists(SEEN):
        os.remove(SEEN)
    work = os.path.join(TMP, "work")
    os.makedirs(work, exist_ok=True)
    env = {"PATH": os.environ["PATH"], "HOME": TMP}
    return subprocess.run([main.LAUNCHER, *args], env=env, cwd=work, capture_output=True, text=True)


def test_launcher():
    # Without Nvidia's app nothing is prepared.
    check("app missing", run(plugin.status())["app"], False)
    check("no prepare without the app", run(plugin.prepare(220)), {"ok": False, "why": "app"})
    check("no list written", os.path.exists(main.LAUNCH_FILE), False)
    open(APP_THERE, "w").close()
    check("answer believed for a minute", run(plugin.status())["app"], False)
    main._app = (None, False)
    check("app found", run(plugin.status())["app"], True)

    run(plugin._main())
    check("launcher installed", mode(main.LAUNCHER), 0o700)
    for bad in (999, 0, -1, "220 --evil", None, 1.5, True):
        check("prepare %r" % (bad,), run(plugin.prepare(bad)), {"ok": False, "why": "game"})
    check("still no list", os.path.exists(main.LAUNCH_FILE), False)
    check("not prepared yet", launch("19111").returncode, 2)

    check("prepare", run(plugin.prepare(220)),
          {"ok": True, "exe": main.LAUNCHER, "start_dir": main.RUNTIME, "options": "19111"})
    check("list is private", mode(main.LAUNCH_FILE), 0o600)
    with open(main.LAUNCH_FILE) as f:
        check("only that game in the list", json.load(f), {"19111": True})

    # The launcher hands Nvidia's app one argument, built from the number alone.
    done = launch("19111")
    check("launcher ran", (done.returncode, done.stderr), (0, ""))
    with open(SEEN) as f:
        check("flatpak got one exact route", json.load(f),
              ["run", "com.nvidia.geforcenow", "--url-route=#?cmsId=19111&launchSource=External"])

    # Preparing another game replaces the first.
    check("another game", run(plugin.prepare(400))["options"], "20000")
    check("the first is no longer allowed", launch("19111").returncode, 2)
    check("the second is", launch("20000").returncode, 0)

    # Refusals: nothing reaches flatpak.
    for bad in (["abc"], ["20000", "extra"], [], ["20000 --evil"], ["-1"], ["20000;id"], [""], ["٢٠٠٠٠"], ["21000"],
                ["2" * 13], ["--url-route=#?cmsId=20000"]):
        done = launch(*bad)
        check("refused %r" % (bad,), (done.returncode, os.path.exists(SEEN)), (2, False))
        check("says why %r" % (bad,), done.stderr.startswith("gfn-launch: "))

    # A list others could write is not believed; nor one that is not a plain file.
    os.chmod(main.LAUNCH_FILE, 0o622)
    done = launch("20000")
    check("list writable by others", (done.returncode, os.path.exists(SEEN)), (2, False))
    check("reason", done.stderr.strip(), "gfn-launch: the game list is not private")
    os.chmod(main.LAUNCH_FILE, 0o660)
    check("list writable by the group", launch("20000").returncode, 2)
    os.chmod(main.LAUNCH_FILE, 0o600)
    check("private again", launch("20000").returncode, 0)
    real = main.LAUNCH_FILE + ".real"
    os.rename(main.LAUNCH_FILE, real)
    os.symlink(real, main.LAUNCH_FILE)
    check("a link is not the list", launch("20000").returncode, 2)
    os.remove(main.LAUNCH_FILE)
    with open(main.LAUNCH_FILE, "w") as f:
        json.dump({"20000": "yes"}, f)
    os.chmod(main.LAUNCH_FILE, 0o600)
    check("only a plain yes counts", launch("20000").returncode, 2)

    # A launcher someone changed is put back.
    with open(main.LAUNCHER, "w") as f:
        f.write("#!/bin/sh\n")
    os.chmod(main.LAUNCHER, 0o777)
    check("prepare again", run(plugin.prepare(400))["ok"], True)
    with open(main.LAUNCHER, "rb") as a, open(os.path.join(REPO, "bin", "gfn-launch"), "rb") as b:
        check("launcher restored", a.read() == b.read())
    check("and private", mode(main.LAUNCHER), 0o700)


JPEG = b"\xff\xd8\xff\xe0" + os.urandom(2000)
PNG = b"\x89PNG\r\n\x1a\n" + os.urandom(2000)
SHOPS = [[
    {"title": "Half-Life\u00ae 2", "images": {"GAME_BOX_ART": ART + "apps/a/box.jpg", "HERO_IMAGE": ART + "apps/a/hero.png",
                                            "KEY_ART": ART + "apps/a/key.jpg"},
     "variants": [("EPIC", "hl2", "19112"), ("STEAM", "220", "19111")]},
    {"title": "Fortnite\u00ae", "images": {"GAME_BOX_ART": ART + "apps/f/box.jpg", "KEY_ART": ART + "apps/f/key.jpg"},
     "variants": [("XBOX", "9x", "30101"), ("EPIC", "fn", "30100")]},
    # Pictures that are not on Nvidia's picture server.
    {"title": "Pok\u00e9mon Caf\u00e9", "images": {"GAME_BOX_ART": "https://evil.example/x.jpg",
                                                 "HERO_IMAGE": ART[:-1] + ".evil.example/h.jpg"},
     "variants": [("STEAM", "900", "30200")]},
    {"title": "  Tab\tbed \n name " + "x" * 300, "images": {"GAME_BOX_ART": ART[:-1] + "@evil.example/x.jpg",
                                                          "HERO_IMAGE": ART + "a b.jpg", "KEY_ART": ART + "a?b=c",
                                                          "TV_BANNER": 5},
     "variants": [("UPLAY", "u", "30300")]},
    {"title": "Quoted", "images": {"GAME_BOX_ART": ART + 'a".jpg', "HERO_IMAGE": ART + "a\\b.jpg"}, "variants": [("GOG", "g", "30310")]},
    # No name, an odd shop, a number that is not one: no row; the Steam link stays.
    {"title": 7, "variants": [("STEAM", "901", "30400")]},
    {"title": "", "variants": [("EPIC", "e", "30401")]},
    {"title": "Odd shop", "images": "none", "variants": [("epic games", "x", "30500"), ("EPIC", "x", "abc"), ("EPIC", "x", 30501)]},
] + [[("EPIC", "f%d" % n, str(40000 + n))] for n in range(25)]]


def launch_list():
    with open(main.LAUNCH_FILE) as f:
        return json.load(f)


def test_chosen():
    # Left by the launcher's checks: the last game started from a Steam page.
    check("last prepared", launch_list(), {"20000": True})
    nvidia.pages = SHOPS
    for n in range(25):
        SHOPS[0][8 + n] = {"title": "Filler %02d" % n, "variants": SHOPS[0][8 + n]}
    check("read with other shops", run(plugin.refresh())["ok"], True)
    check("Steam links as before", kept()["games"], {"220": "19111", "900": "30200", "901": "30400"})
    check("file still private", mode(main.CATALOGUE_FILE), 0o600)
    rows = kept()["rows"]
    check("a row per edition", sorted(k for k in rows if k < "40000"),
          ["19111", "19112", "30100", "30101", "30200", "30300", "30310"])
    check("row", rows["19111"], ["Half-Life\u00ae 2", "STEAM", ART + "apps/a/box.jpg", ART + "apps/a/hero.png"])
    check("wide picture falls back", rows["30100"], ["Fortnite\u00ae", "EPIC", ART + "apps/f/box.jpg", ART + "apps/f/key.jpg"])
    for n in ("30200", "30300", "30310"):
        check("hostile pictures dropped %s" % n, rows[n][2:], ["", ""])
    check("name tidied and cut", rows["30300"][0], ("Tab bed name " + "x" * 300)[:120])

    # Search: by name, whatever the capitals, accents and signs; Steam's edition first.
    check("found", run(plugin.search("half life")), [{"id": "19111", "title": "Half-Life\u00ae 2", "store": "STEAM"},
                                                     {"id": "19112", "title": "Half-Life\u00ae 2", "store": "EPIC"}])
    check("capitals and signs", [g["id"] for g in run(plugin.search("  HALF-LIFE 2 "))], ["19111", "19112"])
    check("accents", [g["id"] for g in run(plugin.search("pokemon cafe"))], ["30200"])
    check("accents typed", [g["id"] for g in run(plugin.search("CAF\u00c9"))], ["30200"])
    check("other shops", [(g["id"], g["store"]) for g in run(plugin.search("fortnite"))], [("30100", "EPIC"), ("30101", "XBOX")])
    check("starts with it first", [g["title"] for g in run(plugin.search("fi"))][:2], ["Filler 00", "Filler 01"])
    check("at most twenty", len(run(plugin.search("filler"))), 20)
    check("nothing by that name", run(plugin.search("zelda")), [])
    check("sixty characters are taken", len(run(plugin.search("x" * 60))), 1)
    check("as long as allowed still matches", len(run(plugin.search("tab bed name " + "x" * 47))), 1)
    for bad in ("a", "  a  ", "", "x" * 61, 5, None, ["ab"], {"a": 1}, True, "\u00ae\u00ae", "--"):
        check("search %r" % (bad,), run(plugin.search(bad)), [])
    before = kept()
    run(plugin.search("$(touch %s/pwned); `id`" % TMP))
    check("the text goes nowhere", (os.path.exists(os.path.join(TMP, "pwned")), kept()), (False, before))

    # Where the launcher is, for a new entry.
    check("entry", run(plugin.entry()), {"ok": True, "exe": main.LAUNCHER, "start_dir": main.RUNTIME})

    # Choosing: only a game of the catalogue, with the Steam entry made for it.
    check("none chosen", run(plugin.chosen()), [])
    for bad in (999, 0, -1, "abc", None, True, 1.5, "30100 ", ["30100"], "../30100"):
        check("choose %r" % (bad,), run(plugin.choose(bad, 111)), {"ok": False})
    for bad in (0, -5, "x", None, True, 1.5, [111], 2**40):
        check("choose for entry %r" % (bad,), run(plugin.choose("30100", bad)), {"ok": False})
    check("still none", run(plugin.chosen()), [])
    check("choose", run(plugin.choose("30100", 3000000111)), {"ok": True})
    check("choose by number", run(plugin.choose(19112, 3000000222)), {"ok": True})
    check("not twice", run(plugin.choose("30100", 3000000333)), {"ok": False})
    check("chosen, by name", run(plugin.chosen()), [
        {"id": "30100", "title": "Fortnite\u00ae", "store": "EPIC", "appid": 3000000111},
        {"id": "19112", "title": "Half-Life\u00ae 2", "store": "EPIC", "appid": 3000000222}])
    check("kept with the settings", os.path.dirname(main.CHOSEN_FILE), decky.DECKY_PLUGIN_SETTINGS_DIR)
    check("chosen file is private", mode(main.CHOSEN_FILE), 0o600)
    check("no half-written copy", os.path.exists(main.CHOSEN_FILE + ".tmp"), False)

    # The launcher may open every chosen game and the last one started from a Steam page.
    check("launcher's list", launch_list(), {"30100": True, "19112": True, "20000": True})
    check("list is private", mode(main.LAUNCH_FILE), 0o600)
    check("a chosen game starts", launch("30100").returncode, 0)
    with open(SEEN) as f:
        check("its route", json.load(f), ["run", "com.nvidia.geforcenow", "--url-route=#?cmsId=30100&launchSource=External"])
    check("Steam pages still work", run(plugin.prepare(220))["options"], "19111")
    check("list after a Steam page", launch_list(), {"30100": True, "19112": True, "19111": True})
    check("the one before is out", launch("20000").returncode, 2)
    check("chosen ones stay", launch("19112").returncode, 0)

    # A chosen game outlives a catalogue that drops it, and a restart.
    nvidia.pages = PAGES
    check("catalogue without them", run(plugin.refresh())["ok"], True)
    check("not found any more", run(plugin.search("fortnite")), [])
    check("still chosen", [g["id"] for g in run(plugin.chosen())], ["30100", "19112"])
    os.remove(main.LAUNCH_FILE)
    run(plugin._main())
    check("list put back on start", launch_list(), {"30100": True, "19112": True, "19111": True})

    # Removing.
    for bad in (999, "abc", None, True, 0, "30100;id", "40001"):
        check("unchoose %r" % (bad,), run(plugin.unchoose(bad)), {"ok": False})
    check("unchoose", run(plugin.unchoose("30100")), {"ok": True})
    check("not twice", run(plugin.unchoose("30100")), {"ok": False})
    check("one left", [g["id"] for g in run(plugin.chosen())], ["19112"])
    check("list without it", launch_list(), {"19112": True, "19111": True})
    check("no longer starts", launch("30100").returncode, 2)
    check("the last one", run(plugin.unchoose(19112)), {"ok": True})
    check("only the Steam page's", launch_list(), {"19111": True})

    # A file someone damaged is not believed.
    with open(main.CHOSEN_FILE, "w") as f:
        json.dump({"last": "1; id", "games": {"30100": {"title": "Fortnite", "store": "EPIC", "appid": 5}, "x": {},
                                            "7": {"title": "", "store": "EPIC", "appid": 5}, "8": "no",
                                            "9": {"title": "T", "store": "EPIC", "appid": "$(id)"}}}, f)
    check("only what is whole", run(plugin.chosen()), [{"id": "30100", "title": "Fortnite", "store": "EPIC", "appid": 5}])
    run(plugin._main())
    check("and nothing else for the launcher", launch_list(), {"30100": True})
    with open(main.CHOSEN_FILE, "w") as f:
        f.write("[]")
    check("broken: none", run(plugin.chosen()), [])


def test_art():
    nvidia.pages = SHOPS
    run(plugin.refresh())
    nvidia.pictures = {"/apps/a/box.jpg": JPEG, "/apps/a/hero.png": PNG, "/apps/f/box.jpg": b"<html>not a picture</html>"}
    got = run(plugin.art("19111", "box"))
    check("cover", (got["ok"], got["kind"], base64.b64decode(got["data"]) == JPEG), (True, "jpg", True))
    got = run(plugin.art(19111, "hero"))
    check("wide picture", (got["ok"], got["kind"], base64.b64decode(got["data"]) == PNG), (True, "png", True))
    check("not a picture", run(plugin.art("30100", "box")), {"ok": False})
    check("not there", run(plugin.art("30100", "hero")), {"ok": False})

    # Too large, exactly at the limit, and a redirect.
    main.ART_BYTES = 3000
    nvidia.pictures["/apps/f/key.jpg"] = (JPEG + bytes(3000))[:3001]
    check("too large", run(plugin.art("30100", "hero")), {"ok": False})
    nvidia.pictures["/apps/f/key.jpg"] = (JPEG + bytes(3000))[:3000]
    check("as large as allowed", run(plugin.art("30100", "hero"))["ok"], True)
    main.ART_BYTES = 6 * 1024 * 1024
    nvidia.pictures["/apps/f/key.jpg"] = (302, [("Location", "http://127.0.0.1:%d/x.jpg" % other.server_port)])
    check("redirect refused", run(plugin.art("30100", "hero")), {"ok": False})
    check("nothing went elsewhere", Elsewhere.hits, [])

    # Games without a picture on Nvidia's server, and what is not a game or a kind of picture.
    nvidia.asked.clear()
    for n in ("30200", "30300", "30310"):
        for kind in ("box", "hero"):
            check("no picture %s %s" % (n, kind), run(plugin.art(n, kind)), {"ok": False})
    for bad in ("BOX", "../box", "", None, 2, ["box"], "logo", True):
        check("kind %r" % (bad,), run(plugin.art("19111", bad)), {"ok": False})
    for bad in (999, "abc", None, True, 0, "19111/../x", "https://evil.example/x.jpg"):
        check("picture of %r" % (bad,), run(plugin.art(bad, "box")), {"ok": False})
    check("nothing was asked for those", nvidia.asked, [])
    for bad in ("https://evil.example/x.jpg", "http://127.0.0.1:%d/x.jpg" % other.server_port, ART[:-1] + "@evil.example/x.jpg",
                ART + "a b.jpg", ART + "a?x=1", ART, "", None, 5, "file:///etc/passwd"):
        check("address %r" % (bad,), main._fetch_art(bad), None)
    check("still nothing asked", (nvidia.asked, Elsewhere.hits), ([], []))
    # The real picture server is the only one allowed outside these checks.
    check("picture server", main.ART_BASE == ART and "ART_BASE = \"https://img.nvidiagrid.net/\"" in open(
        os.path.join(REPO, "main.py")).read())


try:
    test_catalogue()
    test_stale()
    test_caps()
    test_launcher()
    test_chosen()
    test_art()
    print("ok: %d checks" % checks)
finally:
    server.shutdown()
    other.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
