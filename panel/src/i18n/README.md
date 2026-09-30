# Translating the console

The console speaks English and Spanish. There is no i18n library: catalogs are typed
TypeScript objects, English is the source of truth, and `tsc` refuses a key that does not
exist, a missing parameter, or a Spanish catalog with a key too many or too few.

```
i18n/
  en/<namespace>.ts   English, `as const` (the types are derived from it); bundled
  en/index.ts         one `export { ns } from "./ns";` line per namespace
  es/<namespace>.ts   Spanish, typed `Catalog<typeof en>`; loaded as its own chunk
  es/index.ts         the same lines, same order
```

## Using it

```tsx
import { useT } from "../../i18n";

const t = useT();                                   // re-renders on a language switch
t("nav.apps.label");                                // "Applications" / "Aplicaciones"
t("time.duration.hoursMinutes", { hours: 1, minutes: 12 });
t("nav.failed", { count: 3 });                      // plural: Intl.PluralRules picks the form
t.rich("ns.savedTo", { path: <Mono>{path}</Mono> }); // placeholders that are elements
formatBytes(n, t.locale);                           // lib/format takes the language

// Outside a component (a toast from a mutation, a validation helper):
import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";
translate(getLocale(), "language.loadFailed");
```

Data structures hold keys, not text (`label: PlainKey`, see `app/nav.ts`), and whoever
renders them calls `t(item.label)`.

Tests run in English (`test/setup.ts` resets the language after each test). To test in
Spanish: `await act(() => setLocale("es"))` from `app/locale`, then assert Spanish text.

## Adding a namespace

1. `en/myarea.ts`:
   ```ts
   /** What these strings are for. */
   export const myarea = {
     title: "Backups",
     restored: "Restored {name}",
     count: { one: "{count} backup", other: "{count} backups" },
   } as const;
   ```
2. `es/myarea.ts`:
   ```ts
   import type { myarea as en } from "../en/myarea";
   import type { Catalog } from "../types";

   export const myarea: Catalog<typeof en> = {
     title: "Copias de seguridad",
     restored: "Se restauró {name}",
     count: { one: "{count} copia de seguridad", other: "{count} copias de seguridad" },
   };
   ```
3. Add `export { myarea } from "./myarea";` to `en/index.ts` and `es/index.ts`, in
   alphabetical order. Forgetting the Spanish line fails `tsc` in `catalogs.ts`.
4. `npm run typecheck && npx vitest run src/i18n` (the catalog tests check for empty text,
   identical placeholders in both languages, and complete plurals).

## Rules

- **Whole sentences, never fragments.** One key per sentence with `{placeholders}`; never
  build a sentence from pieces (`"Go to " + label.toLowerCase()`), because word order and
  agreement change between languages. A sentence with a link or code inside is still one key,
  rendered with `t.rich`.
- **Everything a person reads or hears**: visible text, `aria-label`, `sr-only` text, live
  region announcements, toasts, `title`s, document titles, placeholders, validation messages.
- **System output is never translated.** What nginx, systemd, certbot, psql, git or WASM's
  server prints (errors, hints, diagnosis, journal lines) is shown verbatim. Only the words
  around it (a heading, "Suggested fix") come from the catalog.
- **Not translated either**: commands (`wasm config set ...`), paths, unit names, product
  names, and language autonyms ("English", "Español").
- **Numbers, sizes, dates and times** go through `lib/format.ts`, never `toFixed` or a
  hard-coded `"en-US"`. A placeholder value is printed as given: format numbers first.
- **Plurals** are `{ one, other }` objects selected by `count`; Spanish may add `many`.
  The keys `one` and `other` are reserved for plurals.
- **Sentence case** in both languages ("Domains and certificates", "Copias de seguridad").
- **Keep the English exactly as it was** when converting a component: tests and the E2E
  suite pin it.

## Spanish style

Neutral international Spanish, addressing the operator as **tú** (the register of GitHub's
Spanish docs and of most developer tools): "Comprueba la conexión", not "Compruebe". No
*vosotros*, no regionalisms. Imperatives on buttons are infinitives ("Guardar cambios",
"Desplegar"), as Spanish interfaces usually write them.

### Glossary

| English | Spanish | Notes |
|---|---|---|
| application, app | aplicación | "app" only where space is tight |
| deploy (verb) | desplegar | |
| deploy, deployment (noun) | despliegue | |
| release | release (la, las releases) | WASM's `releases/<id>` directory. Not "versión": that is WASM's own version and an app's |
| rollback, roll back | reversión, revertir | "Revertir a esta release" |
| health check, health gate | comprobación de salud | |
| unit (systemd) | unidad | |
| service | servicio | |
| job | tarea | background jobs |
| cron job | tarea programada | the page stays "Cron" |
| backup | copia de seguridad | plural "copias de seguridad" |
| restore | restaurar | |
| preview (PR preview) | vista previa | |
| pull request, commit, push, branch | pull request, commit, push, rama | GitHub's own Spanish usage |
| repository | repositorio | |
| build (noun / verb) | compilación / compilar | |
| logs | registros | the journal stays "journal" |
| environment variables | variables de entorno | |
| domain, certificate | dominio, certificado | |
| settings | ajustes | |
| sign in, sign out | iniciar sesión, cerrar sesión | |
| sudo mode, "Confirm it's you" | modo sudo, "Confirma que eres tú" | |
| account, role | cuenta, rol | "security officer" is "responsable de seguridad" |
| passkey | llave de acceso | the name Apple, Google and Microsoft use in Spanish |
| emergency access (the access token) | acceso de emergencia | |
| approval, four-eyes | aprobación, cuatro ojos | "request" is "solicitud" |
| audit log | registro de auditoría | the settings entry is "Auditoría" |
| terms of use (the usage notice) | condiciones de uso | |
| two-factor authentication | verificación en dos pasos | |
| webhook, token, API token | webhook, token, token de API | |
| server, machine | servidor, máquina | |
| overview | resumen | |
