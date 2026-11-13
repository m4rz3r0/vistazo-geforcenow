# Vistazo GeForce NOW

An add-on for [Vistazo](https://github.com/m4rz3r0/vistazo-decky), the calm home screen and game pages for the Steam Deck. It adds a **GeForce NOW** button to Vistazo's page of a Steam game, beside Steam's own play button, when Nvidia's service carries that game. Pressing it opens the game in Nvidia's own GeForce NOW app.

It is a separate Decky plugin so that only those who use GeForce NOW carry its code.

## What it needs

- **Vistazo**. The button and the settings section show inside Vistazo; without it this plugin only has its own panel in the Quick Access menu, which says so.
- **Nvidia's GeForce NOW app for the Steam Deck**, which Nvidia offers on its GeForce NOW download page. The add-on does not install it. Without the app no button shows.

## How it works

- Once a day the add-on reads Nvidia's public list of games and keeps, on the Deck, which Steam games are on it. The settings show how many there are and when the list was read, with a button to read it again.
- The button shows only when the game is on that list and Nvidia's app is installed. There is no greyed-out button otherwise.
- Games start through one Steam entry of the add-on's own, "GeForce NOW · Vistazo". Steam counts the play time on that entry, not on the game.

## Your account and your privacy

- **The add-on never sees your Nvidia account.** You sign in once in Nvidia's own app, the first time it opens; the add-on only opens that app. It stores no name, password or session.
- The only thing it asks the network for is Nvidia's public list of games. The question carries a country and a language ("US", English) and nothing about you, your Deck or your library.
- It talks to that one address only, checks its certificate and does not follow redirects.

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
