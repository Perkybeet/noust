import { describe, expect, it } from "vitest";

import { configGetCommand, configSetCommand, shellQuote } from "./shell";

describe("the terminal form of a setting", () => {
  it("leaves plain values bare", () => {
    expect(configSetCommand("backup.max_per_app", 12)).toBe("noust config set backup.max_per_app 12");
    expect(configSetCommand("apps_directory", "/var/www/apps")).toBe("noust config set apps_directory /var/www/apps");
    expect(configSetCommand("ssl.email", "ops@example.com")).toBe("noust config set ssl.email ops@example.com");
    expect(configSetCommand("notifications.enabled", false)).toBe("noust config set notifications.enabled false");
  });

  it("quotes what a shell would split or expand", () => {
    expect(shellQuote("")).toBe("''");
    expect(shellQuote("/srv/my apps")).toBe("'/srv/my apps'");
    expect(shellQuote("$HOME")).toBe("'$HOME'");
    expect(shellQuote("it's")).toBe(`'it'\\''s'`);
  });

  it("reads a key", () => {
    expect(configGetCommand("backup")).toBe("noust config get backup");
  });
});
