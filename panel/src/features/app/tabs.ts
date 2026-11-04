/**
 * What an adopted site has to show: its state, its logs and its use of the machine. It has no
 * deployments to list, no environment Noust writes, no database it links and no domains it
 * manages (checklist B-85). The application's column reads it (`app/columns.ts`); the sections
 * themselves are the register's (`features/nav/app.ts`).
 */
export const ADOPTED_TABS: ReadonlySet<string> = new Set(["/apps/$domain", "/apps/$domain/logs", "/apps/$domain/metrics", "/apps/$domain/settings"]);
