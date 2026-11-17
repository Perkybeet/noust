/**
 * What a server found when the fleet looked for new versions of the images: the central keeps, for each server, how many images it
 * looked at, how many have a newer version and which (`job.result.nodes[].items[0]`). A server that did not look (no Docker, or a Noust
 * before 4.3) has no item, and is not a failure: it has nothing to say. Pure, and tolerant of a shape it does not know.
 */

export interface ServerLook {
  server: string;
  looked: number;
  newer: number;
  references: string[];
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

/**
 * The servers that looked, with what each found.
 *
 * @param result - The result of the central's job, whatever shape it has.
 * @returns One row for each server that looked, in the order the job lists them.
 */
export function lookResultOf(result: unknown): ServerLook[] {
  const nodes = record(result)?.["nodes"];
  if (!Array.isArray(nodes)) return [];
  return nodes.flatMap((entry): ServerLook[] => {
    const node = record(entry);
    const items: unknown = node?.["items"];
    const item = Array.isArray(items) ? record(items[0]) : null;
    if (node === null || item === null || typeof node["node"] !== "string") return [];
    const looked = item["looked"];
    const newer = item["newer"];
    if (typeof looked !== "number" || typeof newer !== "number") return [];
    const references = item["newer_references"];
    return [{ server: node["node"], looked, newer, references: Array.isArray(references) ? references.filter((reference): reference is string => typeof reference === "string") : [] }];
  });
}
