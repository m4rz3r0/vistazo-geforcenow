import { useEffect, useState } from "react";
import { about, art, choose, chosen, entry, unchoose } from "./backend";
import type { Chosen, Found } from "./backend";
import { safe } from "./safe";
import { vistazo } from "./vistazo";
import { genre, lang, shop } from "./i18n";
import type { AddonGame, AddonShelf } from "./vistazo";

declare const SteamClient: any;
declare const appStore: any;

// The games the player chose to have in Steam: any game GeForce NOW carries, also from other
// shops. Each is a Steam entry of its own whose program is the add-on's launcher and whose
// launch options are Nvidia's number for the game, so Steam's own play button starts it.
const COVER = 0; // Steam's numbers for an entry's pictures
const WIDE = 1;

let games: Chosen[] = [];
const watchers = new Set<() => void>();

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const overview = (appid: number) => safe(() => appStore.GetAppOverviewByAppID(appid), null);

function set(next: Chosen[]) {
  games = next;
  watchers.forEach((fn) => fn());
  // Vistazo's home shows them on a shelf: it is told to look again.
  safe(() => vistazo()?.changed(), undefined);
}

export const chosenGames = (): Chosen[] => games;
export const isChosen = (id: string): boolean => games.some((g) => g.id === id);

export async function loadChosen(): Promise<void> {
  set(await chosen());
}

// Re-renders whoever uses it when the chosen games change.
export function useChosen(): Chosen[] {
  const [, turn] = useState(0);
  useEffect(() => {
    const fn = () => turn((n) => n + 1);
    watchers.add(fn);
    return () => {
      watchers.delete(fn);
    };
  }, []);
  return games;
}

// The add-on's shelf for Vistazo's home: the chosen games that are still in Steam.
export function shelves(): AddonShelf[] {
  const apps = games.map((g) => g.appid).filter((appid) => !!overview(appid));
  return apps.length ? [{ id: "geforcenow", label: "GeForce NOW", apps }] : [];
}

// Whether a Steam entry is one of the chosen games: its page is then drawn by Vistazo, like
// every other game's, instead of Steam's plain one.
export const owns = (appid: number): boolean => games.some((g) => g.appid === appid);

// What that page shows: where the game is played and from which shop, and what Nvidia's
// catalogue says of it. Steam's own play button starts it, as before.
export async function game(appid: number): Promise<AddonGame | null> {
  const mine = games.find((g) => g.appid === appid);
  if (!mine) return null;
  const from = shop(mine.store);
  const known = await about(mine.id, lang).catch(() => null);
  return {
    platform: from ? `GeForce NOW · ${from}` : "GeForce NOW",
    genres: known?.ok ? (known.genres ?? []).map(genre).filter(Boolean) : [],
    summary: (known?.ok && known.summary) || undefined,
  };
}

// Forgets the chosen games whose Steam entry is gone (removed in Steam itself).
export async function dropLost(): Promise<void> {
  await loadChosen();
  // Only when Steam can really be asked: otherwise every game would look gone.
  if (safe(() => typeof appStore.GetAppOverviewByAppID, "") !== "function") return;
  const lost = games.filter((g) => !overview(g.appid));
  if (!lost.length) return;
  for (const g of lost) await unchoose(g.id).catch(() => null);
  await loadChosen();
}

// One of the game's pictures onto its Steam entry. A game without it simply goes without.
async function dress(appid: number, id: string, kind: "box" | "hero", type: number): Promise<void> {
  const picture = await art(id, kind).catch(() => null);
  if (picture?.ok && picture.data && picture.kind) {
    await safe(() => SteamClient.Apps.SetCustomArtworkForApp(appid, picture.data, picture.kind, type), undefined);
  }
}

// Makes the Steam entry of a game the player picked. False when it could not; nothing is left
// in Steam then.
export async function addGame(found: Found): Promise<boolean> {
  if (isChosen(found.id)) return true;
  const launcher = await entry();
  if (!launcher.ok || !launcher.exe || !launcher.start_dir) return false;
  // Steam takes little from this call: the name, the program and its folder are set after it.
  const appid = Number(await SteamClient.Apps.AddShortcut(found.title, launcher.exe, "", "")) || 0;
  if (!appid) return false;
  try {
    for (let i = 0; i < 20 && !overview(appid); i++) await wait(100);
    SteamClient.Apps.SetShortcutName(appid, found.title);
    SteamClient.Apps.SetShortcutExe(appid, launcher.exe);
    SteamClient.Apps.SetShortcutStartDir(appid, launcher.start_dir);
    // All Steam is told about the game: Nvidia's number for it.
    SteamClient.Apps.SetAppLaunchOptions(appid, found.id);
    await dress(appid, found.id, "box", COVER);
    await dress(appid, found.id, "hero", WIDE);
    const kept = await choose(found.id, appid);
    if (!kept.ok) throw new Error("not kept");
  } catch (e) {
    console.warn("[Vistazo GeForce NOW] add", e);
    safe(() => SteamClient.Apps.RemoveShortcut(appid), undefined);
    return false;
  }
  await loadChosen().catch(() => null);
  return true;
}

// Takes a chosen game out: its pictures, its Steam entry and the launcher's leave to open it.
export async function removeGame(game: Chosen): Promise<void> {
  safe(() => SteamClient.Apps.ClearCustomArtworkForApp(game.appid, COVER), undefined);
  safe(() => SteamClient.Apps.ClearCustomArtworkForApp(game.appid, WIDE), undefined);
  safe(() => SteamClient.Apps.RemoveShortcut(game.appid), undefined);
  await unchoose(game.id).catch(() => null);
  await loadChosen().catch(() => null);
}
