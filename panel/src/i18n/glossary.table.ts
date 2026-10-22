/**
 * The glossary of the interface as data, for the tests that hold the catalogs to it
 * (`verbs.test.ts`, `glossary.test.ts`). The words themselves are documented in README.md,
 * "The words of the interface", and a change to a term is made there first (docs/DESIGN.md G-5):
 * `verbs.test.ts` fails on a row that README does not carry.
 *
 * Imports nothing and is imported by no production file, so it never reaches a bundle.
 */

/** One verb or state: the English word of a button or a pill, and the Spanish forms the glossary accepts for it. */
export interface GlossaryRow {
  /** The English text of a key, exactly: the key holds only this word. */
  en: string;
  /** What Spanish says for it: the canonical word first, then its agreements (Fallido, Fallida). */
  es: readonly string[];
  group: "action" | "state" | "answer";
}

export const GLOSSARY: readonly GlossaryRow[] = [
  // Actions (README, "Actions")
  { en: "Update", es: ["Actualizar"], group: "action" },
  { en: "Rebuild", es: ["Recompilar"], group: "action" },
  { en: "Retry", es: ["Reintentar"], group: "action" },
  { en: "Try again", es: ["Intentar de nuevo"], group: "action" },
  { en: "Run again", es: ["Ejecutar de nuevo"], group: "action" },
  { en: "Restart", es: ["Reiniciar"], group: "action" },
  { en: "Stop", es: ["Detener"], group: "action" },
  { en: "Start", es: ["Iniciar"], group: "action" },
  { en: "Roll back", es: ["Volver atrás", "Revertir"], group: "action" },
  { en: "Deploy", es: ["Desplegar"], group: "action" },
  { en: "Delete", es: ["Eliminar"], group: "action" },
  { en: "Remove", es: ["Quitar"], group: "action" },
  { en: "Forget", es: ["Olvidar"], group: "action" },
  { en: "Revoke", es: ["Revocar"], group: "action" },
  { en: "Clear", es: ["Vaciar"], group: "action" },
  { en: "Discard", es: ["Descartar"], group: "action" },
  { en: "Dismiss", es: ["Cerrar"], group: "action" },
  { en: "Create", es: ["Crear"], group: "action" },
  { en: "Add", es: ["Añadir"], group: "action" },
  { en: "New", es: ["Nuevo", "Nueva"], group: "action" },
  { en: "Close", es: ["Cerrar"], group: "action" },
  { en: "Done", es: ["Hecho", "Hecha"], group: "action" },
  { en: "Continue", es: ["Continuar"], group: "action" },
  { en: "Back", es: ["Atrás"], group: "action" },
  { en: "Refresh", es: ["Recargar"], group: "action" },
  { en: "Renew", es: ["Renovar"], group: "action" },
  { en: "Acknowledge", es: ["Reconocer"], group: "action" },
  { en: "Reopen", es: ["Reabrir"], group: "action" },
  { en: "Next", es: ["Siguiente", "Próxima"], group: "action" },
  // States (README, "States"); a state agrees with its entity, so the feminine and the plural count
  { en: "Running", es: ["En ejecución"], group: "state" },
  { en: "In progress", es: ["En curso"], group: "state" },
  { en: "Failed", es: ["Fallido", "Fallida", "Fallidos", "Fallidas", "Con fallos"], group: "state" },
  { en: "Succeeded", es: ["Correcto", "Correcta", "Correctos", "Correctas"], group: "state" },
  { en: "Live", es: ["En producción", "En directo"], group: "state" },
  { en: "Queued", es: ["En cola"], group: "state" },
  { en: "Stopped", es: ["Detenido", "Detenida", "Detenidos", "Detenidas"], group: "state" },
  { en: "Deploying", es: ["Desplegando"], group: "state" },
  { en: "Warning", es: ["Aviso"], group: "state" },
  { en: "Static", es: ["Estático"], group: "state" },
  { en: "Unknown", es: ["Desconocido"], group: "state" },
  // Answers (README, "States")
  { en: "Yes", es: ["Sí"], group: "answer" },
  { en: "No", es: ["No"], group: "answer" },
];

/** A value the glossary retired: it is counted wherever it is written, and the count only goes down. */
export interface Retired {
  id: string;
  language: "en" | "es";
  /** Matched against the whole text of a message (a plural, its `other` form). */
  pattern: RegExp;
  /** Only a label or a title is judged, not a sentence of prose: at most this many words. */
  maxWords?: number;
  /** A text that ends a sentence (a full stop, a question, an exclamation) is prose, and is left alone. */
  notSentence?: true;
  /** What to write instead, for the failure message. */
  instead: string;
  /** Keys that may keep the word, each with the reason: the system's own phrase, a proper name, a deliberate old name. */
  except?: readonly { key: RegExp; why: string }[];
}

/**
 * Places that keep an old name on purpose, whatever is retired: the palette finds a screen by the
 * name it had (NV-5), so the keywords of a destination and the map of renamed screens still say it.
 */
export const OLD_NAMES_KEPT: readonly { key: RegExp; why: string }[] = [
  { key: /(?:^|\.)\w*[kK]eywords$/, why: "a destination keeps its old name as a palette keyword for at least one minor release (NV-5)" },
  { key: /^nav\.renamed\./, why: "the map of renamed screens is where the old names are written down (NV-5)" },
];



/**
 * A Spanish text with the endings of agreement taken off its words ("Fallido", "Fallida", "Fallidos" and
 * "Fallidas" are one stem, "Pendiente" and "Pendientes" too): a gender or a number is not a different
 * translation (README, rule 7 of "The words of the interface").
 */
export function agreementStem(text: string): string {
  return text.trim().toLowerCase().replace(/(?<=\p{L})[oae]s?(?!\p{L})/gu, "");
}

/**
 * English texts that Spanish rightly says more than one way, each with why. Everything else that diverges is
 * a breach: one English text, one Spanish translation. An entry whose text no longer diverges fails
 * `glossary.test.ts`, so this list cannot go stale.
 */
export const DIVERGENCE_EXCEPTIONS: Readonly<Record<string, string>> = {
  "Audit log": "the settings entry is \"Auditoría\" and the log itself \"Registro de auditoría\" (README, glossary)",
  Backups: "a tab or a column header says \"Copias\"; the name of the place says \"Copias de seguridad\"",
  Build: "a noun (\"Compilación\", a section) and a verb (\"Compilar\", the label of a command)",
  Change: "a noun (\"Cambio\", a column) and a verb (\"Cambiar\", a button)",
  Check: "a noun (\"Comprobación\", a column or a constraint) and a verb (\"Comprobar\", a button)",
  Kind: "\"Tipo\" is what a thing is; \"Clase\" is a relation's kind, next to its data type, which is \"Tipo\"",
  Live: "two senses the glossary keeps apart: \"En producción\" (the version that serves) and \"En directo\" (a stream)",
  Open: "a verb (\"Abrir\", a button) and a firewall verdict (\"Abierto\")",
  Reboot: "a noun (\"Reinicio\", a column) and a verb (\"Reiniciar\", a button)",
  "Removed {name}": "a person takes the personal a (\"Se quitó a {name}\"), a thing does not",
  Runs: "\"Se ejecuta\" (when it runs) and \"Ejecuciones\" (the runs, a plural noun)",
  Search: "a noun (\"Búsqueda\", a category) and a verb (\"Buscar\", a placeholder)",
  Tag: "a Git tag keeps \"tag\" and an application's label is \"etiqueta\" (README, glossary)",
  To: "a limit in time is \"Hasta\"; a direction in a diagram is \"Hacia\"",
  View: "a verb (\"Ver\", a button) and a noun (\"Vista\", a view of a database)",
};

export const RETIRED: readonly Retired[] = [
  // English
  { id: "redeploy", language: "en", pattern: /\bRedeploy\b/, instead: "Update (pulls the code and builds) or Rebuild (the same commit again)" },
  { id: "deploy-again", language: "en", pattern: /\bDeploy again\b/, instead: "Update, or Rebuild for the same commit" },
  { id: "update-now", language: "en", pattern: /\bUpdate now\b/, instead: "Update" },
  { id: "retry-variants", language: "en", pattern: /^Retry (?:now|the failed|certificate)\b/, instead: "Retry (with its object when it is ambiguous: Retry failed)" },
  { id: "reading", language: "en", pattern: /^Reading\b/, notSentence: true, instead: "Loading … (every loading text, also what is read from the machine)" },
  { id: "make-verb", language: "en", pattern: /^Make\b/, maxWords: 6, notSentence: true, instead: "New (a file, a folder) or Create (Noust makes it)" },
  { id: "confirm-or-ok", language: "en", pattern: /^(?:Confirm|Confirm and continue|OK)$/, instead: "Continue, or the verb of what is confirmed; a state is Succeeded or Correct" },
  { id: "health-check", language: "en", pattern: /\bhealth checks?\b/i, instead: "Startup check (the system's own phrase stays where the system prints it)" },
  { id: "unit-label", language: "en", pattern: /^(?:Systemd unit|Unit)\b/, maxWords: 4, notSentence: true, instead: "Service (the unit's own name stays a system value)" },
  {
    id: "emergency-access",
    language: "en",
    pattern: /\bEmergency access\b/,
    instead: "Access token (the recovery notice is one line of help)",
    except: [{ key: /^auditEvents\.action\.auth_break_glass$/, why: "the audit trail names the use of the access token as break-glass use, which is what it is" }],
  },
  { id: "deliveries", language: "en", pattern: /\bDeliveries\b/, instead: "Pushes received, Sent messages or Deploy limits, by what it holds" },
  { id: "open-capitalised", language: "en", pattern: /^Open (?:Approvals|Accounts|Activity)$/, instead: "sentence case: Open approvals, Open accounts, Open activity" },
  { id: "learn-more", language: "en", pattern: /^Learn more\b/, instead: "a link that names its destination (NT-8)" },
  { id: "old-names", language: "en", pattern: /^(?:Domains and certificates|Console access|Roles and access|Log shipping|Live version)$/, instead: "the 4.0 names: Domains, Console address, Roles, Logs and metrics export, Live now" },
  // Spanish
  { id: "borrar", language: "es", pattern: /\bborrar(?:l[aeo]s?|se)?\b/i, instead: "Eliminar (Delete) or Quitar (unlink; Clear filters is Quitar filtros)" },
  { id: "leyendo", language: "es", pattern: /^Leyendo\b/, notSentence: true, instead: "Cargando …" },
  { id: "parar-arrancar", language: "es", pattern: /^(?:Parar|Arrancar)\b/, maxWords: 4, notSentence: true, instead: "Detener or Iniciar" },
  { id: "fallo-verbo", language: "es", pattern: /^falló$/i, instead: "Fallido or Fallida: a state is not a verb" },
  { id: "en-vivo", language: "es", pattern: /\ben vivo\b/i, instead: "En producción (the version that serves) or En directo (a stream)" },
  { id: "ganchos", language: "es", pattern: /\bganchos?\b/i, instead: "hook, hooks de despliegue" },
  { id: "dueno", language: "es", pattern: /\bdueñ[oa]s?\b/i, instead: "propietario, propietaria" },
  { id: "vuestro", language: "es", pattern: /\bvuestr[oa]s?\b|\bvosotr[oa]s\b/i, instead: "tú: the interface addresses one person" },
  {
    id: "trabajo-job",
    language: "es",
    pattern: /(?<!\bde )(?<!\bmismo )\btrabajos?\b/i,
    instead: "tarea (a job); \"directorio de trabajo\" and \"espacio de trabajo\" are not jobs",
    except: [
      { key: /^gitlab\.dialog\.nameDescription$/, why: "\"trabajo\" is the example name the sentence offers, not the word for a job" },
      { key: /^auth\.passkeys\.nameHint$/, why: "\"Portátil del trabajo\" is the example name of a passkey: work, not a job" },
      { key: /^auth\.security\.masterBreakGlass$/, why: "\"el trabajo diario\" is everyday work, not a job" },
      { key: /^consoleAccess\.state\.unfinishedBody$/, why: "\"parte del trabajo\" is part of the work to do, not a job" },
    ],
  },
  {
    id: "log-word",
    language: "es",
    pattern: /\blogs?\b/i,
    instead: "registro, registros (the journal stays \"journal\")",
    except: [{ key: /^appPages\.logs\.containersFailedHint$/, why: "it quotes the command docker compose logs" }],
  },
  { id: "ok-or-bien", language: "es", pattern: /^(?:OK|Bien)$/, instead: "Correcto or Correcta" },
  { id: "volver-back", language: "es", pattern: /^Volver$/, instead: "Atrás (a step) or Volver atrás (roll back)" },
  { id: "reconstruir", language: "es", pattern: /\bReconstruir\b/, instead: "Recompilar" },
  { id: "abrir-capitalised", language: "es", pattern: /^Abrir (?:Aprobaciones|Cuentas|Actividad)$/, instead: "mayúscula de frase: Abrir aprobaciones, Abrir cuentas, Abrir actividad" },
  { id: "mas-informacion", language: "es", pattern: /^(?:Más información|Saber más)\b/, instead: "un enlace que nombra su destino (NT-8)" },
  { id: "old-names", language: "es", pattern: /^(?:Dominios y certificados|Acceso a la consola|Roles y accesos|Envío de logs|Versión en producción)$/, instead: "los nombres de la 4.0: Dominios, Dirección de la consola, Roles, Exportación de registros y métricas, Ahora en producción" },
];
