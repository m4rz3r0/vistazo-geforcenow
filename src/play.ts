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
    return [{ id: "geforcenow", label: t.play, run: () => play(appid) }];
  } catch {
    return [];
  }
}
