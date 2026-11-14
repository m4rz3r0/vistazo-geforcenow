import { callable } from "@decky/api";

// What the backend answers. It takes game numbers only: never a path, an address or free text.
export interface Status {
  app: boolean; // Nvidia's app is on the Deck
  games: number; // Steam games in the catalogue
  age: number | null; // seconds since the catalogue was read; null: never
  refreshing: boolean;
}
export interface Prepared {
  ok: boolean;
  why?: "game" | "app" | "error";
  exe?: string;
  start_dir?: string;
  options?: string; // Nvidia's number for the game, digits only
}

// A game of Nvidia's catalogue, in one shop's edition.
export interface Found {
  id: string; // Nvidia's number for that edition
  title: string;
  store: string; // "STEAM", "EPIC"…
}
// A game the player chose, with the Steam entry made for it.
export interface Chosen extends Found {
  appid: number;
}
export interface Picture {
  ok: boolean;
  data?: string; // base64
  kind?: "jpg" | "png";
}

export const status = callable<[], Status>("status");
export const game = callable<[appid: number], { ok: boolean; id?: string }>("game");
export const refresh = callable<[], Status & { ok: boolean }>("refresh");
export const prepare = callable<[appid: number], Prepared>("prepare");
// The one call that takes text: what the player typed, only compared with the games' names.
export const search = callable<[text: string], Found[]>("search");
export const entry = callable<[], { ok: boolean; exe?: string; start_dir?: string }>("entry");
export const chosen = callable<[], Chosen[]>("chosen");
export const choose = callable<[id: string, appid: number], { ok: boolean }>("choose");
export const unchoose = callable<[id: string], { ok: boolean }>("unchoose");
// What the catalogue says of a chosen game, in the page's language.
export interface About {
  ok: boolean;
  store?: string;
  genres?: string[]; // as Nvidia names them: "ROLE_PLAYING"
  summary?: string;
}
export const about = callable<[id: string, language: "es" | "en"], About>("about");
export const art = callable<[id: string, kind: "box" | "hero"], Picture>("art");
