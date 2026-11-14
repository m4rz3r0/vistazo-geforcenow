import { toaster } from "@decky/api";
import { game, prepare, status } from "./backend";
import { t } from "./i18n";
import { safe } from "./safe";
import type { AddonAction } from "./vistazo";

declare const SteamClient: any;
declare const appStore: any;
declare const SteamUIStore: any;

// Every game starts through one Steam entry of the add-on's own: Steam counts play time there.
const ENTRY_NAME = "GeForce NOW · Vistazo";
const ENTRY_KEY = "vistazo-geforcenow:entry";
const NON_STEAM = 1073741824; // how Steam marks an entry that is not from its shop

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const overview = (appid: number) => safe(() => appStore.GetAppOverviewByAppID(appid), null);

// The add-on's entry in Steam, made the first time and found again after that. 0: Steam did not
// make it.
async function entry(exe: string, startDir: string): Promise<number> {
  let appid = Number(safe(() => localStorage.getItem(ENTRY_KEY), "")) || 0;
  // The one remembered may have been removed in Steam since.
  if (appid && overview(appid)?.app_type !== NON_STEAM) appid = 0;
  if (!appid) {
    // Steam takes little from this call: the name, the program and its folder are set after it.
    appid = Number(await SteamClient.Apps.AddShortcut(ENTRY_NAME, exe, "", "")) || 0;
    if (!appid) return 0;
    for (let i = 0; i < 20 && !overview(appid); i++) await wait(100);
    SteamClient.Apps.SetShortcutName(appid, ENTRY_NAME);
    safe(() => localStorage.setItem(ENTRY_KEY, String(appid)), undefined);
  }
  SteamClient.Apps.SetShortcutExe(appid, exe);
  SteamClient.Apps.SetShortcutStartDir(appid, startDir);
  return appid;
}

// Nvidia's mark and its green: on the page the button is that mark, as the service's own app
// shows it, and its name is said beside the button hints.
const MARK =
  "M8.948 8.798v-1.43a6.7 6.7 0 0 1 .424-.018c3.922-.124 6.493 3.374 6.493 3.374s-2.774 3.851-5.75 3.851c-.398 0-.787-.062-1.158-.185v-4.346c1.528.185 1.837.857 2.747 2.385l2.04-1.714s-1.492-1.952-4-1.952a6.016 6.016 0 0 0-.796.035m0-4.735v2.138l.424-.027c5.45-.185 9.01 4.47 9.01 4.47s-4.08 4.964-8.33 4.964c-.37 0-.733-.035-1.095-.097v1.325c.3.035.61.062.91.062 3.957 0 6.82-2.023 9.593-4.408.459.371 2.34 1.263 2.73 1.652-2.633 2.208-8.772 3.984-12.253 3.984-.335 0-.653-.018-.971-.053v1.864H24V4.063zm0 10.326v1.131c-3.657-.654-4.673-4.46-4.673-4.46s1.758-1.944 4.673-2.262v1.237H8.94c-1.528-.186-2.73 1.245-2.73 1.245s.68 2.412 2.739 3.11M2.456 10.9s2.164-3.197 6.5-3.533V6.201C4.153 6.59 0 10.653 0 10.653s2.35 6.802 8.948 7.42v-1.237c-4.84-.6-6.492-5.936-6.492-5.936z";
const GREEN = "#76b900";

let starting = false;

// Opens a Steam game in Nvidia's app. Says so when it cannot.
async function play(appid: number): Promise<void> {
  if (starting) return;
  starting = true;
  try {
    const ready = await prepare(appid);
    if (!ready.ok || !ready.exe || !ready.start_dir || !ready.options) {
      const body = ready.why === "game" ? t.notCarried : ready.why === "app" ? t.appMissing : t.notStarted;
      toaster.toast({ title: t.play, body });
      return;
    }
    const own = await entry(ready.exe, ready.start_dir);
    const known = own ? overview(own) : null;
    if (!known) {
      toaster.toast({ title: t.play, body: t.notStarted });
      return;
    }
    // All Steam is told about the game: Nvidia's number for it.
    SteamClient.Apps.SetAppLaunchOptions(own, ready.options);
    await wait(150);
    // Started the way Steam's own play button does. Without the second call Steam's interface
    // stays at "starting".
    SteamClient.Apps.RunGame(known.GetGameID(), "", -1, 100);
    safe(() => SteamUIStore.SetRunningApp(own), undefined);
  } catch (e) {
    console.warn("[Vistazo GeForce NOW] start", e);
    toaster.toast({ title: t.play, body: t.notStarted });
  } finally {
    starting = false;
  }
}

// What Vistazo shows on a Steam game's page: one button, only when Nvidia's app is on the Deck
// and GeForce NOW carries the game. Otherwise nothing at all.
export async function actions(appid: number): Promise<AddonAction[]> {
  try {
    if (!Number.isInteger(appid) || appid <= 0) return [];
    const [state, found] = await Promise.all([status(), game(appid)]);
    if (!state.app || !found.ok) return [];
    return [{ id: "geforcenow", label: t.play, icon: { path: MARK, tone: GREEN }, run: () => play(appid) }];
  } catch {
    return [];
  }
}
