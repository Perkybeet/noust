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

## Shared words

A word the glossary names is one key, read from `common` by every screen (DESIGN 7, TX-4): `common.words.*` holds the
verbs and the labels that came up in forty places (`name`, `cancel`, `close`, `save`, `edit`, `remove`, `delete`,
`editNamed`, `removeNamed`, `clearFilters`, `fromTerminal`, `start`, `stop`, `restart`, `update`, `retry`, `yes`, `no`…), and
the state words are `common.statusPill.*` and `common.deployState.*`. A row menu's label is `common.rowActions`. Write
a new "Cancel" or "Name" as `t("common.words.cancel")`, and add a word to `common.words` when a third screen needs it.
A word that is not quite the same (another Spanish gender, another meaning) keeps its own key, and a key a helper builds
with a prefix (`apps.bulk.verb.${verb}`) keeps its own too. `glossary.test.ts` counts the texts that repeat a shared
word, per namespace, and the count only goes down.

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
- **Sentence case** in both languages ("Backup schedule", "Copias de seguridad").
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
| startup check (was "health check"), health gate | comprobación de arranque, comprobación de salud | The interface says "Startup check" (DESIGN 7). "Health check" is the system's own phrase and stays where the system prints it |
| unit (systemd) | unidad | A label says "Service" ("servicio", the unit name in mono): "Unit" and "Systemd unit" are jargon (DESIGN 7). "Unidad" stays where the unit file itself is meant |
| service | servicio | |
| job | tarea | background jobs |
| cron job | tarea programada | the page stays "Cron" |
| backup | copia de seguridad | plural "copias de seguridad" |
| restore | restaurar | |
| preview (PR preview) | vista previa | |
| pull request, commit, push, branch | pull request, commit, push, rama | GitHub's own Spanish usage |
| repository | repositorio | |
| build (noun / verb) | compilación / compilar | |
| logs | registros | the journal stays "journal"; never "logs" |
| file | archivo | Never "fichero": one word for the same thing in every text, "archivos", "el archivo Compose" |
| terminal | terminal (el terminal, un terminal) | Masculine, as the RAE has it for the computing sense: "Desde un terminal", "Lo mismo, desde un terminal" |
| tag (of an application, of a recipe) | etiqueta | A Git tag keeps "tag" (previews build from a branch, a pull request or a tag) |
| owner | propietario, propietaria | Never "dueño" |
| environment variables | variables de entorno | |
| domain, certificate | dominio, certificado | |
| settings | ajustes | |
| sign in, sign out | iniciar sesión, cerrar sesión | |
| sudo mode, "Confirm it's you" | modo sudo, "Confirma que eres tú" | |
| account, role | cuenta, rol | "security officer" is "responsable de seguridad" |
| passkey | llave de acceso | the name Apple, Google and Microsoft use in Spanish |
| access token (the installer's token) | token de acceso | Not "Emergency access": that was the name of the door and is retired (4.1). The recovery notice is one line of help. Using it is still recorded as break-glass use in the audit trail |
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

## The words of the interface

The glossary above says what a term is called; this one says which **verb** an action takes and which
**name** a thing has, in both languages, so that the same action is never two words and two things
never share one. It extends the glossary above and does not replace it. It is the source: when a term
changes it changes here first (docs/DESIGN.md G-5), and the tests hold the catalogs to it.

### Rules for applying it

1. **One verb per action, one name per thing, in both languages.** A word of the glossary has one
   translation, and a translation is not used for two different English words (`Descartar` is only Discard).
2. **English is the source and what the tests pin.** Changing an English value means updating the tests
   and the e2e selectors that quote it, in the same change ("Keep the English exactly", above).
3. **Whole sentences with placeholders, never fragments.** The `*.loadingLabel` keys that compose
   "Loading {label}" and the `comparator.*` keys are rewritten as sentences.
4. **Sentence case in both languages** (DESIGN 7), no full stop at the end of a title, `…` (U+2026) and
   never three dots, Spanish in *tú*, infinitives on buttons.
5. **The names of destinations** are a noun of one or two words, the same in the sidebar, the title, the
   tab and the palette; the old name stays as a palette keyword (NV-5).
6. **What the system prints is not translated or rewritten** (DESIGN principle 5); around it go the title
   and the fix, which come from the catalog.
7. **Agreement of states in Spanish.** `StatusPill` uses one word per state (`Fallido`); for a feminine
   entity the caller passes a `label` ("Tarea fallida", "Copia fallida"); a state is never a verb ("Falló").
8. **When it applies.** New strings, and the ones a change touches, follow it. The final sweep cleans what
   is left and leaves `glossary.test.ts` green. A retired value that comes back fails the test.

### Actions

| English | Spanish | Means | Retired |
|---|---|---|---|
| **Update** | Actualizar | pulls the code and builds a new release | Update now, Redeploy, Deploy again |
| **Rebuild** | Recompilar | builds **the same commit** again ("Rebuild this commit", "Rebuild anyway"); build is "compilación" | Redeploy, Deploy again, Reconstruir |
| **Retry** | Reintentar | repeats an **action** that failed: a deployment, a certificate, a job ("Retry failed"). On a failed deployment it is the same call as Rebuild with another intention | Retry now, Retry the failed, Retry certificate |
| **Try again** | Intentar de nuevo | reads again after a **read** that failed (`ErrorBlock`) | |
| **Run again** | Ejecutar de nuevo | repeats a **check** (Diagnose) | Volver a ejecutar |
| **Restart**, **Stop**, **Start** | Reiniciar, Detener, Iniciar | the service, with no build | Parar, Dejar de…, Arrancar |
| **Roll back** | Volver atrás (button), revertir (prose) | to an earlier release | |
| **Deploy** | Desplegar | only the wizard's last button; the noun is "deployment", "despliegue" | |
| **Delete** | Eliminar | destroys data that Noust owns; `Drop` is only SQL (also "Eliminar") | Borrar |
| **Remove** | Quitar | unlinks; it still exists elsewhere | Eliminar (as Remove) |
| **Forget** | Olvidar | Noust stops tracking something it did not create and touches nothing (a trusted host, a linked machine) | |
| **Revoke** | Revocar | invalidates a credential | |
| **Clear** | Vaciar ("Clear filters" is "Quitar filtros") | empties a field, a list or a cache | Borrar filtros, Quitar los filtros |
| **Discard**, **Dismiss** | Descartar, Cerrar | Discard: unsaved changes. Dismiss: a notice (in Spanish "Cerrar") | Descartar for both |
| **Create**, **Add**, **New** | Crear, Añadir, Nuevo/a | Create: Noust makes it. Add: links something that exists. New: the page button of a top-level entity | **Make** ("Make a file", "Make it public"): "New file", "New folder" |
| **Close**, **Done** | Cerrar, Hecho | Done only after showing a secret once; Close for everything else | Done for almost anything |
| **Continue** | Continuar | a step of a wizard, with its object when it is ambiguous ("Continue with GitLab") | **Confirm**, **Confirm and continue**, **OK** (DESIGN 5) |
| **Back** | Atrás | the previous step of a wizard or a dialog; pages have breadcrumbs | Volver (it collides with Volver atrás, Roll back) |
| **Refresh**, **Renew** | Recargar, Renovar | Refresh reads again; Renew issues a certificate again | "Renovar las listas de paquetes", Actualizar to refresh |
| **Acknowledge**, **Reopen** | Reconocer, Reabrir | a firing alert: stops the reminders and never resolves it | Take it, Hand it back |
| **Next** | Siguiente, Próxima | Siguiente: the step or the page. Próxima: the next occurrence in time ("Next run") | |
| **Loading …** | Cargando … | everything that loads, also what is read from the machine: the text of `LoadingRegion`, `sr-only`, and the two-second footer (DESIGN 6.6) | **Reading …** / Leyendo … (about eighty keys), the nine fragments "the …" |

With an article or without: the button of a page has none ("Add server") and the title of its dialog has one
("Add a server"); never both for the same button.

### States

| English | Spanish | Where | Retired |
|---|---|---|---|
| **Running** | En ejecución | the service or the application that is up (`common.statusPill.running`) | En curso, En marcha, Ejecutando (as a state) |
| **In progress** | En curso | work that is advancing (a task, a backup) | |
| **Failed** | Fallido/Fallida | a state, in agreement; "Con fallos" in the plural of a count | "Falló" (a verb, not a state), Con fallo |
| **Succeeded** | Correcto/Correcta | a job or a deployment that ended well | "Bien", a bare "OK" |
| **Live** (the version that serves) | En producción | the "Live now" row ("Ahora en producción"), the badge in the list of deployments | En vivo |
| **Live** (a stream of data) | En directo | the "Live" range of Logs | En vivo |
| **Queued**, **Stopped**, **Deploying**, **Warning**, **Static**, **Unknown** | En cola, Detenido, Desplegando, Aviso, Estático, Desconocido | the `StatusPill` states | |
| **Yes** / **No** | Sí / No | one key `common.yes` and `common.no`, or a `StatusPill` when it is the state of a feature (DESIGN 6.1) | five duplicated pairs |

### Names of destinations and sections

| English (4.0) | Spanish | Before | Note |
|---|---|---|---|
| Domains | Dominios | Domains and certificates | five network tabs; the long name described only the first |
| Recipes, Installed recipes | Recetas, Recetas instaladas | Catalog (a sidebar destination) | "Catálogo" becomes a keyword |
| Channels, Alert rules | Canales, Reglas de alerta | Notifications, Alerts | "Notificaciones" and "Alertas" are keywords |
| Console address | Dirección de la consola | Console access | it does not collide with an application's "Access" |
| Roles | Roles | Roles and access | avoids the third "Access" |
| Controls, ENS report | Controles, Informe ENS | Compliance (two destinations with one name) | |
| Sign-in security | Seguridad de inicio de sesión | Security (the account's) | the server's "Security" (firewall, SSH) and "Service security" of an application do not change meaning |
| Service security | Seguridad del servicio | Unit security | "unit" is jargon |
| Cloud providers | Proveedores en la nube | Cloud | |
| Logs and metrics export | Registros y métricas (the command keeps "Exportación de registros y métricas") | Log shipping ("Envío de logs") | the only place where Spanish said "logs" in navigation. The list of settings is 200px and shows about 21 characters of a name: the long form was cut ("Exportación de registro…"), so the sidebar and the page say the short one |
| Probes and status page | Sondas y estado | | "Sondas y página de estado" was cut at "Sondas y página de est…" |
| About Noust | Acerca de Noust | About | |
| Inbox, Needs you, Recent | Bandeja, Requiere tu atención, Recientes | (new) | |
| Account | Cuenta | (new) | |
| Temporary address | Dirección temporal | (new) | "for trying out" |
| Live now | Ahora en producción | Live version | |
| Startup check | Comprobación de arranque | Health check | nine keys |
| Pushes received | Push recibidos | Deliveries (with three meanings) | what Noust sends to a channel is "Sent messages" / "Mensajes enviados", and the deployment limits ("Delivery settings") are "Deploy limits" / "Límites de despliegue" |
| Backup schedule | Programación de copias | schedule, backup policy, data policy | one word; "Retention" apart |
| Team, Project, Environment | Equipo, Proyecto, Entorno | (existing) | |

And in Spanish: "Hooks de despliegue" (not "Ganchos"), "tag" for a Git tag and "etiqueta" for an application's
label, "propietario" (not "dueño"), "tarea" (not "trabajo") and "registro" (not "log") for a job and its output,
"tú" (not "vuestras"), and the two sentences of `appSettings.webhook.eventsGithub` and `eventsGitlab` translated.

### Voice and jargon (DESIGN 7)

- Kept: zero "please", "sorry", "oops", "successfully" or "Are you sure"; the buttons are verbs of their
  object; an error is what failed, what to do, and what the system said.
- Sentence case: `Open approvals`, `Open accounts`, `Open activity` (and their four Spanish equivalents),
  and no full stop at the end of a title.
- Jargon: "Unit" and "Systemd unit" as a label become "Service"; "health check" becomes "Startup check";
  "Deliveries" becomes what it holds (above); the table of DESIGN 7 grows with these rows.
- "Emergency access" is no longer the name of the token's door (4.1); the recovery notice is a line of help.

### How it is enforced

| What | How |
|---|---|
| The canonical verbs and states, in both languages | `i18n/verbs.test.ts`: every row of the tables above, in English and in Spanish |
| A retired value does not come back | `i18n/glossary.test.ts`: a list of retired values with their documented exceptions (what the system prints, proper names, the palette keywords that keep an old name) over the English and the Spanish catalogs, with a baseline that only goes down (`npm run design:baseline`) |
| One Spanish translation per English text | the same test, over the English texts of fewer than forty characters that appear four times or more. A gender or a number is not another translation (`agreementStem`: "Fallido", "Fallida" and "Fallidos" are one); the texts Spanish rightly says two ways (a noun and a verb, "Live" as a stream and as the version that serves) are listed with their reason in `glossary.table.ts`, and an entry whose text no longer diverges fails the test |
| A shared word is read, not written again | the same test: a key outside `common` that says what `common.words.*`, `common.statusPill.*` or `common.deployState.*` already say, counted per namespace ("Shared words", above) |
| Style | the same test: no title ending in a full stop or written with a capital on every word, no three dots where `…` goes |
