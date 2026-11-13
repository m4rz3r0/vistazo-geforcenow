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

export const status = callable<[], Status>("status");
export const game = callable<[appid: number], { ok: boolean; id?: string }>("game");
export const refresh = callable<[], Status & { ok: boolean }>("refresh");
export const prepare = callable<[appid: number], Prepared>("prepare");
