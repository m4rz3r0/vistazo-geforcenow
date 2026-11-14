import type { ComponentType } from "react";

// The part of Vistazo's add-on contract (version 1) this add-on uses, copied from Vistazo's
// `src/addons.ts` so that nothing is imported from it: the two plugins only meet on the window.
export const CONTRACT = 1;

// Something to do with a game, shown as a quiet button on its page.
export interface AddonAction {
  id: string;
  label: string;
  // A mark to show instead of the words where room is short (beside the play button): one SVG
  // path for a 24x24 box, and the colour it is drawn in ("#76b900"). The label still names it.
  icon?: { path: string; tone?: string };
  run(): void | Promise<void>;
}

// A shelf of the add-on's own on Vistazo's home.
export interface AddonShelf {
  id: string;
  label: string;
  apps: number[];
}

// What the add-on knows of one of its games, for its page: Vistazo draws it. What is missing is
// simply not shown. Texts come already in the player's language.
export interface AddonGame {
  platform?: string;
  genres?: string[];
  summary?: string;
}

export interface Addon {
  id: string;
  name: string;
  contract: number; // the contract it was written for
  // Whether this non-Steam entry is the add-on's (its page is then Vistazo's, with `game`).
  owns?(appid: number): boolean;
  // The game behind one of its entries.
  game?(appid: number, refresh: boolean): Promise<AddonGame | null>;
  // Things to do on a Steam game's page ("play on …"). None: an empty list.
  actions?(appid: number): Promise<AddonAction[]>;
  // Shelves of its own on the home: the player turns each on in Vistazo's settings.
  shelves?(): AddonShelf[];
  // Its own section in Vistazo's settings (plain Decky rows).
  Settings?: ComponentType;
}

export interface VistazoApi {
  contract: number;
  register(addon: Addon): () => void;
  changed(): void; // the add-on's games changed: whatever shows them reads again
}

const READY = "vistazo:ready";

// Vistazo as it is on the window right now, when it is there and new enough.
export function vistazo(): VistazoApi | null {
  const api = (window as any).Vistazo as VistazoApi | undefined;
  return api && typeof api.register === "function" && api.contract >= CONTRACT ? api : null;
}

// Hands the add-on to Vistazo and keeps it there: Vistazo may load after this plugin, and it
// loads again on every update, announcing itself each time. Returns what takes the add-on out.
export function joinVistazo(addon: Addon): () => void {
  let leave: (() => void) | null = null;
  let joined: VistazoApi | null = null;
  const join = () => {
    const api = vistazo();
    if (!api || api === joined) return;
    try {
      leave = api.register(addon);
      joined = api;
    } catch (e) {
      console.warn("[Vistazo GeForce NOW] not registered", e);
    }
  };
  join();
  window.addEventListener(READY, join);
  // In case the announcement was missed.
  const again = window.setInterval(join, 5000);
  return () => {
    window.removeEventListener(READY, join);
    window.clearInterval(again);
    try {
      leave?.();
    } catch {
      // Vistazo is gone already.
    }
  };
}

// Calls `fn` when Vistazo arrives; returns what stops listening.
export function onVistazoReady(fn: () => void): () => void {
  window.addEventListener(READY, fn);
  return () => window.removeEventListener(READY, fn);
}
