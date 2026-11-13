import { safe } from "./safe";
// Interface text in Spanish or English, following Steam's own interface language
// (Spanish for any Spanish locale, English for everything else), as Vistazo does.
declare const LocalizationManager: any;

const steamLanguage = safe(() => String(LocalizationManager.m_rgLocalesToUse[0] ?? ""), "");
export const lang: "es" | "en" = /^es|^latam|spanish/i.test(steamLanguage || navigator.language) ? "es" : "en";
const num = (n: number) => n.toLocaleString(lang === "es" ? "es-ES" : "en-US");

const en = {
  play: "GeForce NOW",
  games: (n: number) => (n === 1 ? "1 Steam game on GeForce NOW" : `${num(n)} Steam games on GeForce NOW`),
  noGames: "The list of games has not been read yet",
  readNow: "Read just now",
  readMinutes: (n: number) => (n === 1 ? "Read a minute ago" : `Read ${n} minutes ago`),
  readHours: (n: number) => (n === 1 ? "Read an hour ago" : `Read ${n} hours ago`),
  readDays: (n: number) => (n === 1 ? "Read yesterday" : `Read ${n} days ago`),
  readAgain: "Read again",
  reading: "Reading…",
  readFailed: "Nvidia's list did not answer. The last one read is still in use.",
  account:
    "You sign in to GeForce NOW in Nvidia's own app, the first time it opens. Play time is counted on the “GeForce NOW · Vistazo” entry.",
  appMissing:
    "Nvidia's GeForce NOW app is needed and it is not on this Deck. Nvidia offers it on its GeForce NOW download page.",
  needsVistazo: "This add-on needs Vistazo: its button shows on Vistazo's game pages.",
  notStarted: "The game could not be started.",
  notCarried: "GeForce NOW does not carry this game any more.",
  yourGames: "Games of your choice",
  yourGamesHint: "Any game GeForce NOW carries, also from other shops, as an entry of its own in Steam.",
  addGame: "Add a game",
  remove: "Remove",
  searchTitle: "Add a game from GeForce NOW",
  searchLabel: "Name of the game",
  searchHint: "Type at least two letters.",
  nothingFound: "No game by that name on GeForce NOW.",
  added: "Added",
  adding: "Adding…",
  notAdded: "The game could not be added.",
  close: "Close",
};

const es: typeof en = {
  play: "GeForce NOW",
  games: (n) => (n === 1 ? "1 juego de Steam en GeForce NOW" : `${num(n)} juegos de Steam en GeForce NOW`),
  noGames: "La lista de juegos aún no se ha leído",
  readNow: "Leída ahora mismo",
  readMinutes: (n) => (n === 1 ? "Leída hace un minuto" : `Leída hace ${n} minutos`),
  readHours: (n) => (n === 1 ? "Leída hace una hora" : `Leída hace ${n} horas`),
  readDays: (n) => (n === 1 ? "Leída ayer" : `Leída hace ${n} días`),
  readAgain: "Leer de nuevo",
  reading: "Leyendo…",
  readFailed: "La lista de Nvidia no ha respondido. Se sigue usando la última que se leyó.",
  account:
    "La sesión de GeForce NOW se inicia en la aplicación de Nvidia, la primera vez que se abre. El tiempo de juego se cuenta en la entrada «GeForce NOW · Vistazo».",
  appMissing:
    "Hace falta la aplicación GeForce NOW de Nvidia y no está en esta Deck. Nvidia la ofrece en su página de descargas de GeForce NOW.",
  needsVistazo: "Este complemento necesita Vistazo: su botón aparece en las páginas de juego de Vistazo.",
  notStarted: "No se ha podido iniciar el juego.",
  notCarried: "GeForce NOW ya no tiene este juego.",
  yourGames: "Juegos que tú eliges",
  yourGamesHint: "Cualquier juego que tenga GeForce NOW, también de otras tiendas, con su propia entrada en Steam.",
  addGame: "Añadir un juego",
  remove: "Quitar",
  searchTitle: "Añadir un juego de GeForce NOW",
  searchLabel: "Nombre del juego",
  searchHint: "Escribe al menos dos letras.",
  nothingFound: "GeForce NOW no tiene ningún juego con ese nombre.",
  added: "Añadido",
  adding: "Añadiendo…",
  notAdded: "No se ha podido añadir el juego.",
  close: "Cerrar",
};

export const t = lang === "es" ? es : en;

// A shop's name as people say it; "" for Steam, which needs no saying here.
const SHOPS: Record<string, string> = {
  STEAM: "",
  EPIC: "Epic Games",
  XBOX: "Xbox",
  UPLAY: "Ubisoft",
  UBISOFT: "Ubisoft",
  GOG: "GOG",
  EA_APP: "EA",
  ORIGIN: "EA",
  BATTLENET: "Battle.net",
};
export const shop = (store: string): string =>
  SHOPS[store] ?? store.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

// How long ago the list was read, in the largest unit that fits.
export function readAgo(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return t.readNow;
  if (minutes < 60) return t.readMinutes(minutes);
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? t.readHours(hours) : t.readDays(Math.floor(hours / 24));
}
