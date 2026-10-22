// The notice of the packages a build is made of (scripts/bundle-licenses.mjs): which package a module
// belongs to, what is read from it, and that the text is the same for the same packages. The tree of
// packages is made up in a temporary directory, so no test depends on what is installed.

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { LICENSES_FILE, licensesOf, packageRootOf, packageRootsOf, readPackage, renderLicenses, stylesheetPackageRoots } from "./bundle-licenses.mjs";

const MIT = "MIT License\n\nCopyright (c) 2020 Example Authors\n\nPermission is hereby granted, free of charge...";

/** @type {string[]} */
const made = [];

/** @returns {string} */
function project() {
  const dir = mkdtempSync(path.join(tmpdir(), "noust-licenses-"));
  made.push(dir);
  return dir;
}

/**
 * Makes an installed package under node_modules.
 *
 * @param {string} dir The project.
 * @param {string} name
 * @param {Record<string, unknown>} manifest
 * @param {Record<string, string>} [files]
 * @returns {string} The package's directory.
 */
function install(dir, name, manifest, files = {}) {
  const root = path.join(dir, "node_modules", ...name.split("/"));
  mkdirSync(root, { recursive: true });
  writeFileSync(path.join(root, "package.json"), JSON.stringify({ name, version: "1.0.0", ...manifest }));
  for (const [file, text] of Object.entries(files)) writeFileSync(path.join(root, file), text);
  return root;
}

afterEach(() => {
  for (const dir of made.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("the package a module belongs to", () => {
  it("is the folder under node_modules, with its scope", () => {
    expect(packageRootOf("/work/panel/node_modules/react/index.js")).toBe("/work/panel/node_modules/react");
    expect(packageRootOf("/work/panel/node_modules/@base-ui/react/button/Button.js")).toBe("/work/panel/node_modules/@base-ui/react");
  });

  it("is the innermost when a package is installed under another", () => {
    expect(packageRootOf("/w/node_modules/a/node_modules/b/lib/x.js")).toBe("/w/node_modules/a/node_modules/b");
    expect(packageRootOf("/w/node_modules/.pnpm/c@1.0.0/node_modules/c/dist/c.js")).toBe("/w/node_modules/.pnpm/c@1.0.0/node_modules/c");
  });

  it("ignores the prefix and the query a plugin adds, and a Windows path", () => {
    expect(packageRootOf("\0/w/node_modules/react/index.js?commonjs-es-import")).toBe("/w/node_modules/react");
    expect(packageRootOf("C:\\w\\node_modules\\@tanstack\\query-core\\build\\x.js")).toBe("C:/w/node_modules/@tanstack/query-core");
  });

  it("reads a path the bundler gives relative to the project, which starts at node_modules itself", () => {
    expect(packageRootOf("node_modules/@fontsource-variable/mona-sans/files/mona.woff2")).toBe("node_modules/@fontsource-variable/mona-sans");
  });

  it("is nothing for Noust's own sources, the store's folders and a folder merely named like node_modules", () => {
    expect(packageRootOf("/w/panel/src/app/App.tsx")).toBeNull();
    expect(packageRootOf("/w/node_modules/.bin/vite")).toBeNull();
    expect(packageRootOf("/w/node_modules/.pnpm")).toBeNull();
    expect(packageRootOf("/w/my_node_modules/react/index.js")).toBeNull();
    expect(packageRootOf("/w/node_modules/")).toBeNull();
    expect(packageRootOf("/w/node_modules/@scope")).toBeNull();
  });
});

describe("what is read from a package", () => {
  it("is its name, version, declared licence, source and the text of its licence file", () => {
    const dir = project();
    const root = install(dir, "example", { license: "MIT", repository: { url: "git+https://github.com/example/example.git" } }, { LICENSE: `${MIT}\r\n\r\n` });

    expect(readPackage(root)).toEqual({
      name: "example",
      version: "1.0.0",
      license: "MIT",
      repository: "https://github.com/example/example",
      author: null,
      copyrightLines: [],
      licenceTexts: [{ file: "LICENSE", text: MIT }],
      notices: [],
    });
  });

  it("takes the licence from the shapes older packages use, and says unknown when there is none", () => {
    const dir = project();
    expect(readPackage(install(dir, "a", { license: { type: "ISC" } }))?.license).toBe("ISC");
    expect(readPackage(install(dir, "b", { licenses: [{ type: "MIT" }, { type: "Apache-2.0" }] }))?.license).toBe("MIT OR Apache-2.0");
    expect(readPackage(install(dir, "c", {}))?.license).toBe("unknown");
  });

  it("says a licence once when it is in two files, and keeps two that differ", () => {
    const dir = project();
    const same = readPackage(install(dir, "twice", { license: "MIT" }, { LICENSE: MIT, "LICENSE.md": `${MIT}\n` }));
    expect(same?.licenceTexts.map((entry) => entry.file)).toEqual(["LICENSE"]);

    const two = readPackage(install(dir, "dual", { license: "MIT OR Apache-2.0" }, { "LICENSE-MIT": MIT, "LICENSE-APACHE": "Apache License\nVersion 2.0" }));
    expect(two?.licenceTexts.map((entry) => entry.file)).toEqual(["LICENSE-APACHE", "LICENSE-MIT"]);
  });

  it("does not take a licence for a type declaration or a script that is named like one", () => {
    const dir = project();
    const found = readPackage(install(dir, "typed", { license: "MIT" }, { "LICENSE.d.ts": "export {}", "license.js": "module.exports = 1", COPYING: MIT }));
    expect(found?.licenceTexts.map((entry) => entry.file)).toEqual(["COPYING"]);
  });

  it("keeps the NOTICE a package carries, which Apache-2.0 asks to travel with it", () => {
    const dir = project();
    const found = readPackage(install(dir, "apache", { license: "Apache-2.0" }, { LICENSE: "Apache License", NOTICE: "Example\nCopyright 2020 Example" }));
    expect(found?.notices).toEqual([{ file: "NOTICE", text: "Example\nCopyright 2020 Example" }]);
  });

  it("names the author of a package that declares a licence and ships no text, without a mail address", () => {
    const dir = project();
    const found = readPackage(install(dir, "bare", { license: "MIT", author: "Ada Lovelace <ada@example.org> (https://example.org)" }));
    expect(found?.licenceTexts).toEqual([]);
    expect(found?.author).toBe("Ada Lovelace");
    expect(found?.copyrightLines).toEqual([]);
  });

  it("reads the copyright from the head of the entry file when the package has no licence file, which a minifier drops from the bundle", () => {
    const dir = project();
    const root = install(dir, "headed", { license: "MIT", module: "dist/lib.mjs", main: "dist/lib.js" });
    mkdirSync(path.join(root, "dist"));
    writeFileSync(path.join(root, "dist", "lib.mjs"), "//----\n// Lib\n//\n// Copyright (c) 2009 Ada Lovelace\n//\n// Licensed under the MIT license\n");

    expect(readPackage(root)?.copyrightLines).toEqual(["Copyright (c) 2009 Ada Lovelace"]);
  });

  it("does not follow an entry file out of the package, nor read the header of a package that has its licence", () => {
    const dir = project();
    writeFileSync(path.join(dir, "outside.js"), "// Copyright (c) 2001 Somebody Else\n");
    expect(readPackage(install(dir, "escaping", { license: "MIT", main: "../../outside.js" }))?.copyrightLines).toEqual([]);
    mkdirSync(path.join(dir, "node_modules", "licensed"), { recursive: true });
    writeFileSync(path.join(dir, "node_modules", "licensed", "index.js"), "// Copyright (c) 2002 Someone\n");
    expect(readPackage(install(dir, "licensed", { license: "MIT", main: "index.js" }, { LICENSE: MIT }))?.copyrightLines).toEqual([]);
  });

  it("is nothing for a folder that is not a package", () => {
    const dir = project();
    mkdirSync(path.join(dir, "node_modules", "nothing"), { recursive: true });
    expect(readPackage(path.join(dir, "node_modules", "nothing"))).toBeNull();
    const broken = path.join(dir, "node_modules", "broken");
    mkdirSync(broken, { recursive: true });
    writeFileSync(path.join(broken, "package.json"), "{ not json");
    expect(readPackage(broken)).toBeNull();
    const nameless = path.join(dir, "node_modules", "nameless");
    mkdirSync(nameless, { recursive: true });
    writeFileSync(path.join(nameless, "package.json"), "{}");
    expect(readPackage(nameless)).toBeNull();
  });
});

describe("the packages a bundle is made of", () => {
  it("are those that wrote code into a chunk or whose file was copied, and not those shaken out to nothing", () => {
    const dir = project();
    const bundle = {
      "assets/index.js": {
        type: "chunk",
        modules: {
          [path.join(dir, "node_modules/react/index.js")]: { renderedLength: 1200 },
          [path.join(dir, "node_modules/unused/index.js")]: { renderedLength: 0 },
          [path.join(dir, "src/main.tsx")]: { renderedLength: 90 },
        },
      },
      "assets/mona.woff2": { type: "asset", originalFileNames: ["node_modules/@fontsource-variable/mona-sans/files/mona.woff2"] },
      "assets/logo.svg": { type: "asset", originalFileNames: ["src/assets/logo.svg"] },
      "assets/index.css": { type: "asset", originalFileNames: ["index.html"] },
    };

    expect([...packageRootsOf(bundle, dir)].sort()).toEqual([path.join(dir, "node_modules/@fontsource-variable/mona-sans"), path.join(dir, "node_modules/react")]);
  });

  it("include a package whose chunk does not say how much it rendered", () => {
    const dir = project();
    const bundle = { "assets/a.js": { type: "chunk", modules: { [path.join(dir, "node_modules/lib/a.js")]: {} } } };
    expect([...packageRootsOf(bundle, dir)]).toEqual([path.join(dir, "node_modules/lib")]);
  });
});

describe("the packages a stylesheet imports", () => {
  it("are its bare imports, with their scope, and those of the relative stylesheets it imports", () => {
    const dir = project();
    const styles = path.join(dir, "src", "styles");
    mkdirSync(styles, { recursive: true });
    writeFileSync(
      path.join(styles, "app.css"),
      [
        '@import "tailwindcss";',
        '@import "./tokens.css";',
        '@import url("uplot/dist/uPlot.min.css") layer(components);',
        '/* @import "commented-out"; */',
        '@import "https://cdn.example.com/never.css";',
      ].join("\n"),
    );
    writeFileSync(path.join(styles, "tokens.css"), '@import "@scope/theme/base.css";\n@import "./app.css";\n');

    const modules = path.join(dir, "node_modules");
    expect([...stylesheetPackageRoots(path.join(styles, "app.css"), modules)].sort()).toEqual([
      path.join(modules, "@scope/theme"),
      path.join(modules, "tailwindcss"),
      path.join(modules, "uplot"),
    ]);
  });

  it("are none for a stylesheet that does not exist", () => {
    expect(stylesheetPackageRoots("/nowhere/app.css", "/nowhere/node_modules").size).toBe(0);
  });
});

describe("the notice", () => {
  /** @param {string} name @param {string} text */
  const entry = (name, text) => ({ name, version: "2.0.0", license: "MIT", repository: `https://example.org/${name}`, author: null, copyrightLines: [], licenceTexts: text === "" ? [] : [{ file: "LICENSE", text }], notices: [] });

  it("opens with Noust's own credit, spelled the way the whole tree spells it", () => {
    expect(renderLicenses([entry("b", MIT)])).toContain("Copyright (c) 2024-2026 Yago López Prado");
  });

  it("lists each package once, by name, with its licence text after its name", () => {
    const text = renderLicenses([entry("zeta", MIT), entry("alpha", "Alpha licence text")]);

    expect(text.indexOf("alpha 2.0.0")).toBeLessThan(text.indexOf("zeta 2.0.0"));
    expect(text).toContain("2 packages");
    expect(text.indexOf("alpha 2.0.0")).toBeLessThan(text.indexOf("Alpha licence text"));
    expect(text.indexOf("Alpha licence text")).toBeLessThan(text.indexOf("zeta 2.0.0"));
    expect(text).toContain("License: MIT");
    expect(text).toContain("Source: https://example.org/alpha");
    expect(text.endsWith("\n")).toBe(true);
    expect(text.endsWith("\n\n")).toBe(false);
  });

  it("says that a package has no licence file instead of writing one for it", () => {
    const text = renderLicenses([{ ...entry("bare", ""), author: "Ada Lovelace" }]);

    expect(text).toContain("bare declares the licence above in its package.json and carries no licence file.");
    expect(text).toContain("Copyright holder named by the package: Ada Lovelace.");
    expect(text).not.toContain("Permission is hereby granted");
  });

  it("gives a package that says MIT and carries no text the licence's own words under the copyright line it states", () => {
    const text = renderLicenses([{ ...entry("headed", ""), copyrightLines: ["Copyright (c) 2009 Ada Lovelace"] }]);

    expect(text).toContain("MIT License\n\nCopyright (c) 2009 Ada Lovelace\n\nPermission is hereby granted, free of charge");
    expect(text).toContain("The above copyright notice and this permission notice shall be included in all copies");
    expect(text).not.toContain("carries no licence file");
  });

  it("does not give another licence a text it did not declare", () => {
    const text = renderLicenses([{ ...entry("gpl-ish", ""), license: "GPL-3.0", copyrightLines: ["Copyright (c) 2009 Ada Lovelace"] }]);

    expect(text).not.toContain("Permission is hereby granted");
    expect(text).toContain("carries no licence file");
    expect(text).toContain("Copyright (c) 2009 Ada Lovelace");
  });

  it("carries a package's NOTICE under its licence", () => {
    const text = renderLicenses([{ ...entry("apache", "Apache License"), notices: [{ file: "NOTICE", text: "Example product\nCopyright 2020 Example" }] }]);

    expect(text).toContain("NOTICE of apache");
    expect(text.indexOf("Apache License")).toBeLessThan(text.indexOf("Copyright 2020 Example"));
  });

  it("is the same bytes for the same packages, in any order, so two builds of one tree agree", () => {
    const one = renderLicenses([entry("a", MIT), entry("b", MIT)]);
    const other = renderLicenses([entry("b", MIT), entry("a", MIT)]);
    expect(other).toBe(one);
    expect(one).not.toMatch(/20\d\d-\d\d-\d\d|\/tmp\//);
  });
});

describe("the notice of a build", () => {
  it("lists the packages of the chunks, the fonts and the stylesheet, each once, with the text its folder carries", () => {
    const dir = project();
    install(dir, "react", { license: "MIT" }, { LICENSE: "React licence text" });
    install(dir, "@fontsource-variable/mona-sans", { version: "5.3.0", license: "OFL-1.1" }, { LICENSE: "Font licence text" });
    install(dir, "tailwindcss", { license: "MIT" }, { LICENSE: "Tailwind licence text" });
    install(dir, "devtool", { license: "MIT" }, { LICENSE: "Never in the build" });
    mkdirSync(path.join(dir, "src"), { recursive: true });
    writeFileSync(path.join(dir, "src", "app.css"), '@import "tailwindcss";');
    const bundle = {
      "assets/a.js": { type: "chunk", modules: { [path.join(dir, "node_modules/react/index.js")]: { renderedLength: 10 } } },
      "assets/b.js": { type: "chunk", modules: { [path.join(dir, "node_modules/react/jsx-runtime.js")]: { renderedLength: 10 } } },
      "assets/mona.woff2": { type: "asset", originalFileNames: ["node_modules/@fontsource-variable/mona-sans/files/mona.woff2"] },
    };

    const text = licensesOf(bundle, { projectDir: dir, stylesheets: ["src/app.css"] });

    expect(text).toContain("3 packages");
    for (const expected of ["React licence text", "Font licence text", "Tailwind licence text"]) expect(text).toContain(expected);
    expect(text).not.toContain("Never in the build");
    expect(text.match(/^react 1\.0\.0$/gm)).toHaveLength(1);
  });

  it("is written to a file at the root of the build under the name NOTICE gives", () => {
    expect(LICENSES_FILE).toBe("third-party-licenses.txt");
  });
});
