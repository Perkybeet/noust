import { describe, expect, it } from "vitest";

import { ApiError } from "../../../api/errors";
import { readOwn, stateOfError } from "./ownRows";

describe("what a server holds of what is its own", () => {
  it("names the destinations logs are copied to and the secret managers it is connected to", () => {
    expect(readOwn("log-shipping", [{ drains: [{ name: "loki-main" }, { name: "syslog" }] }])).toEqual({ names: ["loki-main", "syslog"] });
    expect(readOwn("secret-managers", [{ providers: [{ kind: "vault" }] }])).toEqual({ names: ["vault"] });
  });

  it("counts deploy keys, which have no name worth listing, and says nothing for a server that holds none", () => {
    expect(readOwn("deploy-keys", [{ keys: [{ id: 1 }, { id: 2 }] }])).toEqual({ names: [], count: 2 });
    expect(readOwn("deploy-keys", [{}])).toEqual({ names: [], count: 0 });
  });

  it("says which code hosts are connected, from what each one reports", () => {
    expect(readOwn("integrations", [{ configured: true }, { connections: [{ id: 1 }] }])).toEqual({ names: ["github", "gitlab"] });
    expect(readOwn("integrations", [{ configured: false }, { connections: [] }])).toEqual({ names: [] });
  });

  it("reads an answer of another shape as holding nothing instead of failing", () => {
    expect(readOwn("log-shipping", [null])).toEqual({ names: [] });
    expect(readOwn("secret-managers", ["unexpected"])).toEqual({ names: [] });
  });
});

describe("why a server could not be read", () => {
  const failure = (status: number) => new ApiError(status, "x", "y");

  it("is an older Noust where the route does not exist, a ceiling where it is forbidden, and silence otherwise", () => {
    expect(stateOfError(failure(404))).toBe("unsupported");
    expect(stateOfError(failure(403))).toBe("refused");
    expect(stateOfError(failure(502))).toBe("off");
    expect(stateOfError(new Error("network"))).toBe("off");
  });
});
