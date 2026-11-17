import { describe, expect, it } from "vitest";

import { translate } from "../i18n/translate";
import { storeUpdateWords, updatedByStore } from "./installedFrom";

const t = ((key: string) => (translate as (locale: string, key: string) => string)("en", key)) as never;

describe("an installation that an app store updates", () => {
  it("is the one the server says was installed from a store, and no other", () => {
    expect(updatedByStore("ugreen-app-center")).toBe(true);
    expect(updatedByStore("a-store-of-the-future")).toBe(true);
    expect(updatedByStore(null)).toBe(false);
    expect(updatedByStore(undefined)).toBe(false);
    expect(updatedByStore("")).toBe(false);
  });

  it("says the new versions arrive through the NAS's App Center, and through the store in general words for one it does not know", () => {
    expect(storeUpdateWords(t, "ugreen-app-center")).toBe("New versions arrive through the App Center of the NAS.");
    expect(storeUpdateWords(t, "a-store-of-the-future")).toBe("New versions arrive through the app store this Noust was installed from.");
  });

  it("says nothing for an installation the console updates itself", () => {
    expect(storeUpdateWords(t, null)).toBeNull();
    expect(storeUpdateWords(t, undefined)).toBeNull();
  });
});
