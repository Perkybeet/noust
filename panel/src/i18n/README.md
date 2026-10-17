# Translating the console

The console speaks English and Spanish. There is no i18n library: catalogs are typed
TypeScript objects, English is the source of truth, and `tsc` refuses a key that does not
exist, a missing parameter, or a Spanish catalog with a key too many or too few.

A third language is **not** another typed catalog: it is a JSON file in `community/` that anyone can
write without TypeScript, held to English by tests instead of by the compiler, and shown in English
key by key where it has not been translated. Registering one is `npm run i18n:new -- pt Português`.
The guide for contributors is [docs/TRANSLATING.md](../../../docs/TRANSLATING.md); the rules below
(whole sentences, placeholders, plurals, what is never translated) apply to a community language too.

```
i18n/
  en/<namespace>.ts   English, `as const` (the types are derived from it); a chunk of its own
  en/index.ts         one `export { ns } from "./ns";` line per namespace (types only at run time)
  es/<namespace>.ts   Spanish, typed `Catalog<typeof en>`; a chunk of its own
  es/index.ts         the same lines, same order
  catalogs.ts         what is bundled (the chrome) and how every other namespace is fetched
  routeNamespaces.gen.ts   which namespaces each route draws: generated, `npm run i18n:routes`
```

## How the texts reach the browser

Every namespace of English and Spanish is a same-origin chunk of its own, fetched with `import()`. Only
the **chrome** is in the script every page loads (`CHROME` in `catalogs.ts`, in English): the namespaces
the frame of any page draws before the router has said which page it is. A namespace is chrome only if a
cold render of the frame needs it; each one costs every visitor, on every page. Spanish, and any other
language, fetches its chrome before the first render (`main.tsx`).

1. **Before a page is shown**, the root route loads the namespaces its routes draw (`routes/__root.tsx`,
   `routeNamespaces.ts`), on every navigation and every hover that preloads one. The page never opens
   in English or in keys, and nothing shifts: the router keeps the old page until the new one is ready.
   The map is generated from the source (`scripts/i18n-routes.mjs`): a route draws every namespace named
   by a literal in the files its page imports, dynamic imports included. A route the map does not know
   loads every namespace, which is slow once and right. Add a page, or let a page draw another
   feature's texts, and run `npm run i18n:routes`; `npm test` fails until you have.
2. **After the first paint**, the rest of the language is fetched while the browser is idle, six chunks at
   a time (`preload.ts`), so the next page waits for nothing. Not on a connection that asked to save data.
3. **Switching language** fetches what the page on screen has loaded, in the new language, and then
   applies it. (A test gets English whole from `test/setup.ts`, so Spanish is whole too.)

### `t()` outside the render

`t()` runs in components, but also in event handlers, toasts, validation helpers and `translate(getLocale(), ...)`
with no component to suspend. So it never waits and never throws. If the namespace of its key is not here yet it
answers with the best text there is: the language's own if it is loaded; otherwise English's, if the language is
not English and English's namespace is loaded; otherwise the key, which is what an unknown key has always been.
Either way it asks for the namespace, so the next call has the text, and a component that drew the stand-in is
drawn again when the namespace arrives (`useT` listens, and only to arrivals somebody was waiting for, so the
idle preload renders nothing). In development the first such miss per namespace is a console warning that names
the key. It is a fallback, not a plan: a page that triggers it has a namespace its route did not declare, and
`routes/namespaces.test.tsx` opens every route on a cold catalog to say which.

A label built from data (the name of an audit event, a compliance control) asks `hasMessage(key)` whether English
has a text before it falls back to the server's own word. Nothing outside `catalogs.ts` imports a catalog:
`architecture.test.ts` fails on the next `import ... from "./en"`.

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

Tests run in English (`test/setup.ts` loads it whole, and resets the language after each test), so `t()`
is as synchronous for whoever writes a test as it ever was. To test in Spanish:
`await act(() => setLocale("es"))` from `app/locale`, then assert Spanish text. To test what a browser that has
just arrived sees, `goCold()` from `test/coldCatalog` leaves English with its chrome alone and records every
`t()` that finds its namespace missing; `drawnKeys()` finds message keys on screen.

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
   alphabetical order. Forgetting the Spanish line fails `tsc` in `catalogs.ts`. Nothing else
   registers the chunks: the build finds the files.
4. `npm run i18n:routes` if a page draws it (it rewrites `routeNamespaces.gen.ts`; commit the result).
5. `npm run typecheck && npx vitest run src/i18n` (the catalog tests check for empty text,
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
| Update (an application) | Actualizar | Pulls the source and builds a new release. Never the system's updates, never Noust's own (below) |
| System updates | Actualizaciones del sistema | The operating system's packages (Server, Overview). Never a bare "Updates" |
| Update Noust | Actualizar Noust | Noust itself, on one server or the whole fleet |
| Restart, Stop, Start | Reiniciar, Detener, Iniciar | The service, with no build. Restart is the same release again |
| Roll back (an application's button) | Volver atrás | An earlier release. The long form in prose is "revertir" |
| From a recipe | Desde una receta | Where a wizard starts from; never "Deploy a recipe", which would be a button that only opens a page |
| Showing 21–40 of 57, Rows per page | Mostrando 21–40 de 57, Filas por página | A list's paging (`common.pagination`) |
| deploy key, SSH key, host key | clave de despliegue, clave SSH, clave de host | An SSH key is a "clave", never a "llave": "llave de acceso" is the passkey |
| deploy queue, place in line, waiting for | cola de despliegues, lugar de la cola, esperando a | What waits its turn. What the queue itself says it waits for (`waiting_for`) is the system's phrase and stays as it came |
| Cancel (a job), Stop the build | Cancelar, Detener compilación | Cancel takes a job out of the queue or stops it; "Stop build" is what the dialog's button says of a build that runs |
| team | equipo | A boundary of who sees and who may act, never of where code runs (`teams` namespace) |
| project | proyecto | Groups applications, databases and sites of one piece of work |
| environment (of a project) | entorno | A stage of a project: production, staging. An application's "Environment" tab is its variables, also "entorno": the page says which |
| team role: viewer, operator, administrator, owner | lector, operador, administrador, propietario | The server's own roles keep "lector", "operador", "administrador" |
| Move (an application) | Mover | To another project or environment; it takes its sites and databases and restarts nothing |
