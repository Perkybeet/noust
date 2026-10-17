# Translating the console

The console speaks English and Spanish. A third language does not need anyone to write TypeScript: it
is one JSON file of translations that the console reads when somebody chooses that language. This
page is the whole guide, from nothing to a pull request.

What is translated is the console: its buttons, messages, headings and help text. Not translated, on
purpose: what a program printed (nginx's, systemd's, git's own words), commands, paths, unit names and
product names. Notifications Noust sends (email, Slack, Telegram) are written by the server in English
or Spanish and are a separate list, not part of this file.

## What you need

- A checkout of the repository and Node 22 (`panel/.nvmrc`); `cd panel && npm ci` once.
- The code of your language, as BCP 47 writes it: `pt` for Portuguese, `pt-BR` for Brazilian
  Portuguese if it differs, `fr`, `de`. Not `en` or `es`, which already exist.

## Start a language

```bash
cd panel
npm run i18n:new -- pt Português
```

It creates `panel/src/i18n/community/pt.json` (empty: `{}`) and adds one line to
`panel/src/i18n/communityLocales.ts` registering `pt` as a **draft**, under the name the language
calls itself (`Português`, never "Portuguese": the people who read it find themselves by it). Those two
changes are all the code a language needs.

## Translate

```bash
npm run i18n:missing -- pt 40     # the next 40 keys, with English as the text to replace
npm run i18n:status -- pt         # how far you are, what is wrong, what comes next
```

`missing` prints a JSON skeleton: paste it into `pt.json` and replace each English text with yours.

```json
{
  "nav.apps.label": "Aplicações",
  "nav.failed": { "one": "{count} falhou", "other": "{count} falharam" }
}
```

- **The key** is the dotted path English uses; never change one. A key English does not have is
  reported (a typo, or a key that was renamed since).
- **Placeholders** such as `{count}` or `{name}` are filled in by Noust: keep each one exactly, in the
  place your language needs it. A missing or misspelled placeholder is refused.
- **Plurals** are an object of forms chosen by the number. Write `one` and `other`, and the others
  your language uses (`many` in Portuguese and French, for example; the checker says which). A form
  your language never selects is refused.
- **Leave out what you have not translated.** The console shows English for any key your file does
  not have, so a language can grow a page at a time. An empty text is refused for that reason: it would
  show nothing.
- **Whole sentences.** Each key is a complete sentence with its placeholders; never join pieces. Word
  order and agreement are yours.
- **Sentence case**, as English and Spanish do ("Domains and certificates"), and the same word for
  the same thing everywhere. The Spanish glossary in [`panel/src/i18n/README.md`](../panel/src/i18n/README.md)
  lists the terms Noust has fixed (release, health gate, unit, sudo mode...) and how Spanish says them; read it before you
  choose yours, and keep your own list of the choices you make.

## See it

The language switch does not offer a draft (nobody should land in a language that is half done), so a
draft is chosen from the browser. With the console open, in the developer tools' console:

```js
localStorage.setItem("noust.locale", "pt");
```

Reload. Run the console against a seeded, sandboxed backend while you work (`python
scripts/console_server.py --port 8080` and `cd panel && npm run dev`, see
[CONTRIBUTING.md](../CONTRIBUTING.md)). Check long words in narrow places (a table header, a button on a
phone) and both themes. `localStorage.removeItem("noust.locale")` goes back to the browser's choice.

## Check it

```bash
npm run i18n:check
```

It runs the same checks `npm test` runs on every contributed file, and so does CI: every key exists in
English, every placeholder is kept, every plural has the forms the language uses, nothing is empty,
and the registry and the files agree. The tests name the key and say what to change.

## Send it

A pull request with `pt.json` and the registry line. It starts as a draft. Say in the description who
translated it and whether a native speaker read it.

**Released** means the language is offered in the switch and chosen for people whose browser asks for
it. A maintainer changes `draft` to `released` in `communityLocales.ts` when at least **95%** of English
is translated (the checker refuses a release below that), a speaker other than the author has read the
pages people see first (sign-in, overview, applications, an application's page, settings), and someone is
willing to keep it current: a release adds keys, and the new ones are shown in English until they are
translated. `npm run i18n:status` shows the gap after an update.

## Why it is built this way

English is the source of truth, and `tsc` holds English and Spanish complete: a missing Spanish key
does not compile. A file that a person writes without a compiler cannot be held that way, so the
tests hold it instead, and a fallback to English keeps a gap from ever showing a key or a blank. A
language lives in its own file and its own chunk: nothing here ships to somebody who reads English.
The cost of a language to the project is the file, and the promise to keep it up to date.

There is no machine translation in the build and no service the console calls to translate. If you
use a tool to draft, read what it wrote: the console is what an operator reads at 3 a.m. with a server
down.
