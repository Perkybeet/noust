import type { policyScope as en } from "../en/policyScope";
import type { Catalog } from "../types";

export const policyScope: Catalog<typeof en> = {
  card: {
    title: "Todas las bases de datos de {engine}",
    description: "Una misma programación para todas, y para las que se creen después.",
    none: "Ninguna programación cubre todas las bases de datos de {engine}. Cada una se protege con su propia programación, o no se protege.",
    coveredBy: "La programación de todas las bases de datos de {engine} copia también esta. Pon una programación propia para cambiar cuándo o dónde.",
    set: "Copiar todas las bases de datos",
    edit: "Editar",
    covers: "Cubre",
    coversThis: "Todas las bases de datos de {engine}, incluida {name}",
    leavesThisOut: "Todas las bases de datos de {engine}, menos {name}",
    overridden: "{name} tiene una programación propia, que esta deja en paz.",
    loadFailed: "No se pudo cargar la programación de todas las bases de datos",
    remove: "Quitar",
    removeTitle: "Quitar la programación de todas las bases de datos de {engine}",
    removeDescription:
      "Ninguna base de datos se copia ya con ella y la programación se detiene. Los volcados ya hechos se quedan. Las bases de datos con programación propia la conservan.",
    removeAction: "Quitar programación",
  },
  drawer: {
    titleAll: "Copias de todas las bases de datos de {engine}",
    descriptionAll:
      "Una política para todas las bases de datos de {engine}, también las que se creen después. Una base de datos con política propia la conserva.",
    leaveOut: "Dejar fuera",
    leaveOutHelp: "Un volcado que deja algo fuera restaura una base de datos sin ello.",
    names: "Un nombre por línea, o separados por comas.",
    databases: "Bases de datos",
    databasesHelp: "Bases de datos enteras que no merece la pena guardar: una copia de pruebas, una caché.",
    schemas: "Esquemas",
    schemasHelp: "Esquemas de PostgreSQL que quedan fuera de cada volcado.",
    tables: "Tablas",
    tablesHelp: "Tablas que quedan fuera de cada volcado. Un nombre puede ir calificado (public.sessions) o ser un patrón (logs_*).",
    collections: "Colecciones",
    collectionsHelp: "Colecciones de MongoDB que quedan fuera de cada volcado.",
    invalid: "No es un nombre válido: {entries}.",
    tooMany: "Como máximo {max} nombres.",
  },
  summary: {
    leavesOut: "Deja fuera",
    databases: "bases de datos {names}",
    schemas: "esquemas {names}",
    tables: "tablas {names}",
    collections: "colecciones {names}",
    nothing: "Nada",
    scope: "Cubre",
    scopeAll: "Todas las bases de datos del motor",
    scopeOne: "Esta base de datos",
  },
};
