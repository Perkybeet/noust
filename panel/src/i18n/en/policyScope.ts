/**
 * Strings of a database backup policy's scope and exclusions: one policy for every database of
 * an engine, and the databases, schemas, tables or collections it leaves out.
 */
export const policyScope = {
  card: {
    title: "Every {engine} database",
    description: "One schedule for all of them, and for the ones created later.",
    none: "No policy covers every {engine} database. Each one is protected by its own policy, or not at all.",
    coveredBy: "The policy for every {engine} database backs this one up. Set a schedule of its own to change when or where.",
    set: "Back up every database",
    covers: "Covers",
    coversThis: "Every {engine} database, {name} included",
    leavesThisOut: "Every {engine} database, but not {name}",
    remove: "Remove",
    removeTitle: "Remove the policy for every {engine} database",
    removeDescription: "No database is backed up by it any more, and the schedule stops. The dumps already taken stay. Databases with a policy of their own keep it.",
    removeAction: "Remove policy",
  },
  drawer: {
    titleAll: "Backups of every {engine} database",
    descriptionAll: "One policy for every database on {engine}, including the ones created later. A database with a policy of its own keeps it.",
    leaveOut: "Leave out",
    leaveOutHelp: "A dump that leaves something out restores a database without it.",
    names: "One name per line, or separated by commas.",
    databases: "Databases",
    databasesHelp: "Whole databases that are not worth keeping: a scratch copy, a cache.",
    schemas: "Schemas",
    schemasHelp: "PostgreSQL schemas left out of every dump.",
    tables: "Tables",
    tablesHelp: "Tables left out of every dump. A name may be qualified (public.sessions) or a pattern (logs_*).",
    collections: "Collections",
    collectionsHelp: "MongoDB collections left out of every dump.",
    invalid: "Not a valid name: {entries}.",
    tooMany: "At most {max} names.",
  },
  summary: {
    leavesOut: "Leaves out",
    databases: "databases {names}",
    schemas: "schemas {names}",
    tables: "tables {names}",
    collections: "collections {names}",
  },
} as const;
