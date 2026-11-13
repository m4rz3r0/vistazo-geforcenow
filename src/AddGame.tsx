import { DialogButton, Focusable, ModalRoot, TextField } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { search } from "./backend";
import type { Found } from "./backend";
import { addGame, useChosen } from "./chosen";
import { shop, t } from "./i18n";

// The dialog where the player looks a game up by name and picks it. Picking one makes its Steam
// entry and closes the dialog.
export function AddGame({ closeModal }: { closeModal?: () => void }) {
  const [text, setText] = useState("");
  const [found, setFound] = useState<Found[] | null>(null);
  const [adding, setAdding] = useState("");
  const [failed, setFailed] = useState(false);
  const chosen = useChosen();
  const asked = useRef(0);

  // Asks a moment after the last letter, and only the last question's answer is shown.
  useEffect(() => {
    const wanted = text.trim();
    const mine = ++asked.current;
    if (wanted.length < 2) {
      setFound(null);
      return;
    }
    const timer = window.setTimeout(() => {
      search(wanted.slice(0, 60))
        .then((list) => mine === asked.current && setFound(list))
        .catch(() => mine === asked.current && setFound([]));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [text]);

  const pick = async (game: Found) => {
    if (adding) return;
    setAdding(game.id);
    setFailed(false);
    const ok = await addGame(game).catch(() => false);
    setAdding("");
    if (ok) closeModal?.();
    else setFailed(true);
  };

  const note = { fontSize: 13, lineHeight: 1.4, opacity: 0.75, padding: "10px 0" };
  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 12 }}>{t.searchTitle}</div>
      <TextField
        label={t.searchLabel}
        value={text}
        focusOnMount
        disabled={!!adding}
        onChange={(e) => setText(e.target.value.slice(0, 60))}
      />
      {failed && <div style={note}>{t.notAdded}</div>}
      {found === null && <div style={note}>{t.searchHint}</div>}
      {found?.length === 0 && <div style={note}>{t.nothingFound}</div>}
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: "50vh", overflowY: "auto", marginTop: 8 }}>
        {(found ?? []).map((game) => {
          const has = chosen.some((g) => g.id === game.id);
          const where = shop(game.store);
          const state = adding === game.id ? t.adding : has ? t.added : where;
          return (
            <DialogButton
              key={game.id}
              disabled={has || !!adding}
              style={{ display: "flex", justifyContent: "space-between", gap: 16, textAlign: "left", height: "auto", padding: "10px 14px" }}
              onClick={() => pick(game)}
              onGamepadFocus={(e: any) => e?.currentTarget?.scrollIntoView?.({ block: "nearest" })}
            >
              <span>{game.title}</span>
              {state && <span style={{ opacity: 0.6, flexShrink: 0 }}>{state}</span>}
            </DialogButton>
          );
        })}
      </Focusable>
      <DialogButton style={{ marginTop: 14 }} disabled={!!adding} onClick={() => closeModal?.()}>
        {t.close}
      </DialogButton>
    </ModalRoot>
  );
}
