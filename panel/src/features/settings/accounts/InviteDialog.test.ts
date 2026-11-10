import { describe, expect, it } from "vitest";

import { invitationLink, isLocalAddress } from "./InviteDialog";

describe("isLocalAddress", () => {
  it.each(["http://localhost:8080", "http://127.0.0.1:8080", "http://127.1.2.3", "http://[::1]:8080", "http://0.0.0.0:8080", "http://console.localhost"])(
    "says %s is one only this computer reaches",
    (address) => {
      expect(isLocalAddress(address)).toBe(true);
    },
  );

  it.each(["https://console.example.com", "http://203.0.113.7:8080", "http://10.0.4.2:8080", "https://localhost.example.com", "not a url", ""])(
    "says %s is not",
    (address) => {
      expect(isLocalAddress(address)).toBe(false);
    },
  );
});

describe("invitationLink", () => {
  it("puts the code after the # of the address it is given", () => {
    expect(invitationLink("noust_inv_abc", "https://console.example.com")).toBe("https://console.example.com/invite#noust_inv_abc");
  });
});
