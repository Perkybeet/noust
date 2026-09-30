import { afterEach, describe, expect, it, vi } from "vitest";

import { CeremonyError, browserAndSystem, isAddress, browserLimit, createPasskey, credentialJson, creationOptions, fromBase64url, getPasskey, requestOptions, toBase64url } from "./webauthn";

function bytes(...values: number[]): ArrayBuffer {
  return new Uint8Array(values).buffer;
}

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "credentials");
});

describe("base64url", () => {
  it("round-trips bytes without padding, with the URL-safe alphabet", () => {
    const value = bytes(0xfb, 0xff, 0xbf, 0x01);
    const encoded = toBase64url(value);
    expect(encoded).toBe("-_-_AQ");
    expect([...new Uint8Array(fromBase64url(encoded))]).toEqual([0xfb, 0xff, 0xbf, 0x01]);
    expect([...new Uint8Array(fromBase64url("-_-_AQ=="))]).toEqual([0xfb, 0xff, 0xbf, 0x01]);
  });

  it("encodes a view of part of a buffer, not the whole buffer", () => {
    const view = new Uint8Array([1, 2, 3, 4]).subarray(1, 3);
    expect(toBase64url(view)).toBe(toBase64url(bytes(2, 3)));
  });
});

describe("options from their JSON form", () => {
  it("converts the members that are bytes when the browser has no parser of its own", () => {
    vi.stubGlobal("PublicKeyCredential", vi.fn());
    const options = creationOptions({
      challenge: "AQID",
      rp: { id: "localhost", name: "Noust" },
      user: { id: "BAU", name: "ana@web-1", displayName: "Noust on web-1" },
      pubKeyCredParams: [{ type: "public-key", alg: -7 }],
      excludeCredentials: [{ type: "public-key", id: "CQkJ", transports: ["internal"] }],
    });
    expect([...new Uint8Array(options.challenge as ArrayBuffer)]).toEqual([1, 2, 3]);
    expect([...new Uint8Array(options.user.id as ArrayBuffer)]).toEqual([4, 5]);
    expect(options.user.name).toBe("ana@web-1");
    expect([...new Uint8Array(options.excludeCredentials?.[0]?.id as ArrayBuffer)]).toEqual([9, 9, 9]);
    expect(options.excludeCredentials?.[0]?.transports).toEqual(["internal"]);

    const request = requestOptions({ challenge: "AQID", rpId: "localhost", allowCredentials: [{ type: "public-key", id: "CQkJ" }] });
    expect([...new Uint8Array(request.allowCredentials?.[0]?.id as ArrayBuffer)]).toEqual([9, 9, 9]);
    expect(request.rpId).toBe("localhost");
  });

  it("leaves it to the browser when it can parse them itself", () => {
    const parseCreationOptionsFromJSON = vi.fn(() => ({ parsed: "creation" }));
    const parseRequestOptionsFromJSON = vi.fn(() => ({ parsed: "request" }));
    vi.stubGlobal("PublicKeyCredential", Object.assign(vi.fn(), { parseCreationOptionsFromJSON, parseRequestOptionsFromJSON }));
    expect(creationOptions({ challenge: "AQID" })).toEqual({ parsed: "creation" });
    expect(requestOptions({ challenge: "AQID" })).toEqual({ parsed: "request" });
    expect(parseCreationOptionsFromJSON).toHaveBeenCalledWith({ challenge: "AQID" });
  });
});

describe("the credential back to JSON", () => {
  it("uses the browser's own toJSON when there is one", () => {
    const credential = { toJSON: () => ({ id: "native" }) } as unknown as Credential;
    expect(credentialJson(credential)).toEqual({ id: "native" });
  });

  it("encodes a registration by hand otherwise, id and rawId alike", () => {
    const credential = {
      id: "ignored",
      rawId: bytes(9, 9, 9),
      type: "public-key",
      authenticatorAttachment: "cross-platform",
      response: { clientDataJSON: bytes(123, 125), attestationObject: bytes(1, 2), getTransports: () => ["usb"] },
      getClientExtensionResults: () => ({ credProps: { rk: true } }),
    } as unknown as Credential;
    expect(credentialJson(credential)).toEqual({
      id: "CQkJ",
      rawId: "CQkJ",
      type: "public-key",
      authenticatorAttachment: "cross-platform",
      clientExtensionResults: { credProps: { rk: true } },
      response: { clientDataJSON: "e30", attestationObject: "AQI", transports: ["usb"] },
    });
  });

  it("encodes an assertion by hand, the user handle included", () => {
    const credential = {
      rawId: bytes(7),
      type: "public-key",
      response: { clientDataJSON: bytes(1), authenticatorData: bytes(2), signature: bytes(3), userHandle: bytes(4) },
    } as unknown as Credential;
    expect(credentialJson(credential)).toMatchObject({
      id: "Bw",
      response: { clientDataJSON: "AQ", authenticatorData: "Ag", signature: "Aw", userHandle: "BA" },
    });
  });
});

describe("ceremonies", () => {
  it("reports the browser's refusal in its own words", async () => {
    vi.stubGlobal("PublicKeyCredential", vi.fn());
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: { get: () => Promise.reject(new DOMException("The request is not allowed by the user agent.", "NotAllowedError")), create: () => Promise.reject(new DOMException("An attempt was made to use an object that is not usable.", "InvalidStateError")) },
    });
    const cancelled = await getPasskey({ challenge: "AQID" }).catch((error: unknown) => error);
    expect(cancelled).toBeInstanceOf(CeremonyError);
    expect(cancelled).toMatchObject({ cancelled: true, detail: "NotAllowedError: The request is not allowed by the user agent." });
    const refused = await createPasskey({ challenge: "AQID", user: { id: "AQ" } }).catch((error: unknown) => error);
    expect(refused).toMatchObject({ cancelled: false, reason: "InvalidStateError" });
  });

  it("never offers one on an address", () => {
    expect(isAddress("127.0.0.1")).toBe(true);
    expect(isAddress("[::1]")).toBe(true);
    expect(isAddress("localhost")).toBe(false);
    expect(isAddress("noust.example.com")).toBe(false);
  });

  it("says when the page cannot start one at all", () => {
    vi.stubGlobal("isSecureContext", false);
    expect(browserLimit()).toBe("insecure_context");
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("PublicKeyCredential", undefined);
    expect(browserLimit()).toBe("no_webauthn");
  });
});

describe("naming a new passkey", () => {
  it.each([
    ["Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0", "Firefox", "Linux"],
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15", "Safari", "macOS"],
    ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36 Edg/129.0", "Edge", "Windows"],
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1", "Safari", "iOS"],
    ["curl/8", "Browser", null],
  ])("%s is %s on %s", (agent, browser, system) => {
    expect(browserAndSystem(agent)).toEqual({ browser, system });
  });
});
