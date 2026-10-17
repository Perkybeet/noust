# Languages contributed as data

One JSON file per language, named by its code (`pt.json`, `pt-BR.json`): dotted keys to translated text, plural
forms as `{ "one": "...", "other": "..." }`. What a file leaves out is shown in English.

Start one with `npm run i18n:new -- pt Português`, see what is left with `npm run i18n:missing -- pt 40`, check it
with `npm run i18n:check`. The whole guide, including how to see your translation in the console, is
[docs/TRANSLATING.md](../../../../docs/TRANSLATING.md). A file is only valid when its language is registered in
`../communityLocales.ts`; the script does both.
