# Vistazo GeForce NOW

An add-on for [Vistazo](https://github.com/m4rz3r0/vistazo-decky), the calm home screen and game pages for the Steam Deck. It adds a **GeForce NOW** button (Nvidia's mark, in its green) to Vistazo's page of a Steam game, right beside Steam's own play button, when Nvidia's service carries that game. Pressing it opens the game in Nvidia's own GeForce NOW app.

The button on Steam game pages and starting a game you added from its own entry have both been tried on a Deck
with Nvidia's app. It is a young add-on all the same: tell us what does not work.

It is a separate Decky plugin so that only those who use GeForce NOW carry its code.

## What it needs

- **Vistazo**. The button and the settings section show inside Vistazo; without it this plugin only has its own panel in the Quick Access menu, which says so.
- **Nvidia's GeForce NOW app for the Steam Deck**, which Nvidia offers on its GeForce NOW download page. The add-on does not install it. Without the app no button shows.

## How it works

- Once a day the add-on reads Nvidia's public list of games and keeps, on the Deck, which Steam games are on it. The settings show how many there are and when the list was read, with a button to read it again.
- The button shows only when the game is on that list and Nvidia's app is installed. There is no greyed-out button otherwise.
- From the settings you can also add any game GeForce NOW carries, by name, including games of other shops (Fortnite from Epic, say). Each gets a Steam entry of its own, with its cover, that Steam's play button starts through Nvidia's app; with Vistazo they show on a GeForce NOW shelf on the home, and their page is Vistazo's like any other game's, with the genre and the few lines Nvidia's list has about the game. Removing one from the settings takes its entry out of Steam.
- Games started from a Steam game's page go through one Steam entry of the add-on's own, "GeForce NOW · Vistazo". Steam counts the play time on that entry, not on the game.

## Your account and your privacy

- **The add-on never sees your Nvidia account.** You sign in once in Nvidia's own app, the first time it opens; the add-on only opens that app. It stores no name, password or session.
- The only things it asks the network for are Nvidia's public list of games and, for a game you add, its cover and wide picture from Nvidia's picture server and, once a week, what Nvidia's list says about that game (asked by Nvidia's number for it, in Spanish or English). What you type to find a game stays on the Deck. The question for the list carries a country and a language ("US", English) and nothing about you, your Deck or your library.
- It talks to those two addresses only, checks their certificates and does not follow redirects.

## Status

First version, not released. How Nvidia's Deck app is told to go straight to one game has not been confirmed on a Deck yet; it lives in one place (`route` in `bin/gfn-launch`).

## Building

```sh
pnpm install
sh pack.sh                      # out/VistazoGeForceNOW.zip
npx tsc --noEmit -p .
python3 tools/test_backend.py   # against a pretend catalogue; never contacts Nvidia
```

## License

BSD 3-Clause. See [LICENSE](LICENSE).

GeForce NOW and the NVIDIA mark shown on the button are trademarks of NVIDIA Corporation, used here only to say
where the game opens. This add-on is not made or endorsed by NVIDIA.
