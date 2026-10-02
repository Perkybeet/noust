#!/usr/bin/env node
// A stand-in for the prisma CLI, for the Prisma scenarios of the integration harness.
//
// It prints what the real one prints for the two commands Noust runs, and it decides what
// "applied" means from files in the project, so a scenario controls it by committing:
//
//   prisma generate         always succeeds.
//   prisma migrate deploy   prisma/FAIL exists: exit 1 with the error a failed migration
//                           prints (P3009).
//                           A migration directory not listed in prisma/applied.txt: apply it
//                           and print the "have been applied" block.
//                           Otherwise: "No pending migrations to apply."
//
// The output is the real tool's, not a summary, because Noust reads it (a migration that
// was applied is what marks a deployment as one that changed the schema).

const fs = require("node:fs");
const path = require("node:path");

const args = process.argv.slice(2);
const root = process.cwd();
const dir = path.join(root, "prisma");
const migrations = path.join(dir, "migrations");

function pending() {
  const applied = fs.existsSync(path.join(dir, "applied.txt"))
    ? fs.readFileSync(path.join(dir, "applied.txt"), "utf8").split("\n").map((l) => l.trim())
    : [];
  return fs
    .readdirSync(migrations, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !applied.includes(e.name))
    .map((e) => e.name)
    .sort();
}

if (args[0] === "generate") {
  console.log("Prisma schema loaded from prisma/schema.prisma");
  console.log("\n✔ Generated Prisma Client to ./node_modules/@prisma/client");
  process.exit(0);
}

if (args[0] === "migrate" && args[1] === "deploy") {
  console.log("Prisma schema loaded from prisma/schema.prisma");
  console.log('Datasource "db": PostgreSQL database "app", schema "public" at "localhost:5432"');
  const all = fs.readdirSync(migrations, { withFileTypes: true }).filter((e) => e.isDirectory());
  console.log(`\n${all.length} migration${all.length === 1 ? "" : "s"} found in prisma/migrations\n`);

  if (fs.existsSync(path.join(dir, "FAIL"))) {
    console.error("Error: P3009\n");
    console.error("migrate found failed migrations in the target database, new migrations will not be applied.");
    process.exit(1);
  }

  const todo = pending();
  if (todo.length === 0) {
    console.log("No pending migrations to apply.");
    process.exit(0);
  }
  for (const name of todo) console.log(`Applying migration \`${name}\``);
  console.log("\nThe following migration(s) have been applied:\n\nmigrations/");
  for (const name of todo) console.log(`  └─ ${name}/\n    └─ migration.sql`);
  console.log("\nAll migrations have been successfully applied.");
  process.exit(0);
}

console.error(`fake prisma: unsupported command: ${args.join(" ")}`);
process.exit(2);
