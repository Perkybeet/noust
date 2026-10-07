/** The engine behind a key: `postgresql@proggest.postgres`, an instance in a container, is PostgreSQL. */
export function engineFamilyOf(engine: string): string {
  return engine.split("@", 1)[0] ?? engine;
}
