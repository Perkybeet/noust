import { describe, expect, it } from "vitest";

import type { ApiToken } from "../../api/queries/auth";
import { bindT, loadCatalog } from "../../i18n";
import { expiryPhrase, sortTokens, tokenState } from "./tokens";

const en = bindT("en");
const NOW = Date.UTC(2026, 8, 25, 12);
const seconds = (ms: number): number => ms / 1000;

function token(id: number, fields: Partial<ApiToken> = {}): ApiToken {
  return { id, name: `t${String(id)}`, scope: "read", created_at: seconds(NOW) - id * 60, expires_at: null, last_used_at: null, revoked_at: null, ...fields };
}

describe("tokens", () => {
  it("is active until it expires or is revoked; revocation wins", () => {
    expect(tokenState(token(1), NOW)).toBe("active");
    expect(tokenState(token(1, { expires_at: seconds(NOW) + 60 }), NOW)).toBe("active");
    expect(tokenState(token(1, { expires_at: seconds(NOW) - 60 }), NOW)).toBe("expired");
    expect(tokenState(token(1, { expires_at: seconds(NOW) - 60, revoked_at: seconds(NOW) - 120 }), NOW)).toBe("revoked");
  });

  it("lists live tokens first, newest first within a state", () => {
    const revoked = token(1, { revoked_at: seconds(NOW) });
    const expired = token(2, { expires_at: seconds(NOW) - 1 });
    const older = token(4);
    const newer = token(3);
    expect(sortTokens([revoked, expired, older, newer], NOW).map((t) => t.id)).toEqual([3, 4, 2, 1]);
  });

  it("says when a token expires", () => {
    expect(expiryPhrase(en, null)).toBe("never expires");
    expect(expiryPhrase(en, seconds(Date.UTC(2026, 11, 24, 12)))).toBe("expires Dec 24, 2026");
  });

  it("says it in Spanish when asked to", async () => {
    await loadCatalog("es");
    const es = bindT("es");
    expect(expiryPhrase(es, null)).toBe("no caduca");
    expect(expiryPhrase(es, seconds(Date.UTC(2026, 11, 24, 12)))).toBe("caduca el 24 dic 2026");
  });
});
