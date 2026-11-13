import { ButtonItem, PanelSection, PanelSectionRow, showModal } from "@decky/ui";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { AddGame } from "./AddGame";
import { refresh, status } from "./backend";
import type { Chosen, Status } from "./backend";
import { dropLost, removeGame, useChosen } from "./chosen";
import { readAgo, shop, t } from "./i18n";
import { onVistazoReady, vistazo } from "./vistazo";

const Note = ({ children }: { children: ReactNode }) => (
  <PanelSectionRow>
    <div style={{ fontSize: 12, lineHeight: 1.4, opacity: 0.75, padding: "4px 0" }}>{children}</div>
  </PanelSectionRow>
);

// The add-on's section, the same in Vistazo's settings and in its own panel: how the list of
// games stands, a button to read it again, where signing in happens, and the games the player
// chose to have in Steam.
export function Settings() {
  const [state, setState] = useState<Status | null>(null);
  const [reading, setReading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [removing, setRemoving] = useState("");
  const chosen = useChosen();

  // A chosen game whose entry was removed in Steam itself is forgotten here too.
  useEffect(() => {
    dropLost().catch(() => undefined);
  }, []);

  const remove = async (game: Chosen) => {
    setRemoving(game.id);
    await removeGame(game).catch(() => undefined);
    setRemoving("");
  };

  useEffect(() => {
    let here = true;
    const look = () =>
      status()
        .then((s) => here && setState(s))
        .catch(() => undefined);
    look();
    // The list may be being read on the side: look again while the rows are on screen.
    const again = window.setInterval(look, 5000);
    return () => {
      here = false;
      window.clearInterval(again);
    };
  }, []);

  const readAgain = async () => {
    setReading(true);
    setFailed(false);
    try {
      const s = await refresh();
      setState(s);
      setFailed(!s.ok);
    } catch {
      setFailed(true);
    } finally {
      setReading(false);
    }
  };

  const busy = reading || !!state?.refreshing;
  return (
    <PanelSection title="GeForce NOW">
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          label={state && state.games > 0 ? t.games(state.games) : t.noGames}
          description={state && state.age !== null ? readAgo(state.age) : undefined}
          disabled={busy || !state}
          onClick={readAgain}
        >
          {busy ? t.reading : t.readAgain}
        </ButtonItem>
      </PanelSectionRow>
      {failed && <Note>{t.readFailed}</Note>}
      {state && !state.app && <Note>{t.appMissing}</Note>}
      <Note>{t.account}</Note>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          label={t.yourGames}
          description={t.yourGamesHint}
          disabled={!state || state.games === 0}
          onClick={() => showModal(<AddGame />)}
        >
          {t.addGame}
        </ButtonItem>
      </PanelSectionRow>
      {chosen.map((game) => (
        <PanelSectionRow key={game.id}>
          <ButtonItem
            layout="below"
            label={game.title}
            description={shop(game.store) || undefined}
            disabled={!!removing}
            onClick={() => remove(game)}
          >
            {t.remove}
          </ButtonItem>
        </PanelSectionRow>
      ))}
    </PanelSection>
  );
}

// The plugin's own panel in the Quick Access menu: the same section, and a line when Vistazo,
// which is where the button shows, is not there.
export function OwnPanel() {
  const [there, setThere] = useState(() => !!vistazo());
  useEffect(() => {
    const look = () => setThere(!!vistazo());
    const stop = onVistazoReady(look);
    // Vistazo does not announce that it left.
    const again = window.setInterval(look, 3000);
    return () => {
      stop();
      window.clearInterval(again);
    };
  }, []);
  return (
    <>
      {!there && (
        <PanelSection>
          <Note>{t.needsVistazo}</Note>
        </PanelSection>
      )}
      <Settings />
    </>
  );
}
