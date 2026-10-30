import type { CodedText } from "../../components/ui/ServerText";

/**
 * A sentence the server says as a code, read from a field the contract types as an open dictionary (a
 * recipe's reason for being unavailable, its warning): the code and its values when it has them, null when
 * it does not, so `ServerText` says the server's own English instead.
 */
export function codedText(value: unknown): CodedText | null {
  return typeof value === "object" && value !== null && typeof (value as { code?: unknown }).code === "string" ? (value as CodedText) : null;
}
