#!/usr/bin/env python3
"""Backend checks against a pretend catalogue, all inside a throwaway folder: `python3 tools/test_backend.py`.

Nothing here talks to Nvidia or touches the real home: `decky` is a stand-in pointing at the
temporary folder, and "flatpak" is a script that only writes down what it was started with."""
import asyncio
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
        self.pages = []  # each: a list of games, each a list of (shop, shop's number, Nvidia's number)
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
        self.reply(405)

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
            "items": [{"variants": [{"appStore": s, "storeId": n, "id": i} for s, n, i in game]} for game in games],
        }}, "padding": "x" * nvidia.padding})


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
    check("asks only for the link", first.endswith("items{variants{appStore storeId id}}}}"))
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


try:
    test_catalogue()
    test_stale()
    test_caps()
    test_launcher()
    print("ok: %d checks" % checks)
finally:
    server.shutdown()
    other.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
