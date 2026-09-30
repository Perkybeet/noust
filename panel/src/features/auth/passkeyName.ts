import type { T } from "../../i18n";
import { browserAndSystem } from "./webauthn";

/** A new passkey's suggested name, in the operator's language: "Firefox on Linux". */
export function suggestedName(t: T, userAgent?: string): string {
  const { browser, system } = browserAndSystem(userAgent);
  return system === null ? browser : t("auth.passkeys.suggestedName", { browser, system });
}
