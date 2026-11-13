import { definePlugin } from "@decky/api";
import { actions } from "./play";
import { OwnPanel, Settings } from "./Settings";
import { CONTRACT, joinVistazo } from "./vistazo";

const NAME = "Vistazo GeForce NOW";

// A cloud with a play mark.
function CloudIcon() {
  return (
    <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true">
      <path
        d="M7 18.5a4.5 4.5 0 0 1-.6-8.96 5.8 5.8 0 0 1 11.2 1.1A3.95 3.95 0 0 1 17 18.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinejoin="round"
      />
      <path d="M10.6 11.2v4.4l3.8-2.2z" fill="currentColor" />
    </svg>
  );
}

export default definePlugin(() => {
  // All this plugin adds to Steam goes through Vistazo: a button on its game pages and a section
  // in its settings. Without Vistazo it only has its own panel.
  const leave = joinVistazo({ id: "geforcenow", name: "GeForce NOW", contract: CONTRACT, actions, Settings });
  return {
    name: NAME,
    titleView: <div>{NAME}</div>,
    content: <OwnPanel />,
    icon: <CloudIcon />,
    onDismount() {
      leave();
    },
  };
});
