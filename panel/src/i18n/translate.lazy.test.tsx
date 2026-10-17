import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resetLocale, setLocale } from "../app/locale";
import { goCold } from "../test/coldCatalog";
import { CHROME, catalogsIdle, dropNamespaces, hasNamespace, loadCatalog, loadNamespaces } from "./catalogs";
import * as en from "./en";
import * as es from "./es";
import { translate } from "./translate";
import { useT } from "./useT";

/**
 * `t()` when its namespace has not arrived. It runs in render, in event handlers, in toasts and in
 * code with no component, so it can neither wait nor throw: it answers with the best text there is,
 * asks for the rest, and a component that drew the stand-in is drawn again when the text is here.
 *
 * `backups.area` is a plain message of a namespace that no page's first frame draws.
 */

let restore: (() => Promise<void>) | undefined;

afterEach(async () => {
  await restore?.();
  restore = undefined;
  await loadCatalog("en");
  resetLocale("en");
});

function cold() {
  const state = goCold();
  restore = state.restore;
  return state;
}

describe("a namespace that is not here yet", () => {
  it("answers with the key in English, never throws, and has the text on the next call", async () => {
    const { misses } = cold();
    expect(hasNamespace("en", "backups")).toBe(false);
    expect(translate("en", "backups.area")).toBe("backups.area");
    expect(misses).toEqual(["en:backups"]);

    await catalogsIdle();
    expect(translate("en", "backups.area")).toBe(en.backups.area);
  });

  it("answers with English where English is here and the language's own is not", async () => {
    const { misses } = cold();
    await loadNamespaces("en", ["backups"]);
    expect(translate("es", "backups.area")).toBe(en.backups.area);
    expect(misses).toEqual(["es:backups"]);

    await catalogsIdle();
    expect(translate("es", "backups.area")).toBe(es.backups.area);
  });

  it("answers with the key when neither language is here, and asks for both", async () => {
    const { misses } = cold();
    expect(translate("es", "backups.area")).toBe("backups.area");
    expect(misses).toEqual(["es:backups", "en:backups"]);

    await catalogsIdle();
    expect(translate("es", "backups.area")).toBe(es.backups.area);
    expect(translate("en", "backups.area")).toBe(en.backups.area);
  });

  it("reports a key that exists nowhere only once its namespace is here: that is a missing text, not a missing chunk", async () => {
    cold();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await loadNamespaces("en", ["backups"]);
    const loose = translate as (locale: "en", key: string) => string;
    expect(loose("en", "backups.nowhere")).toBe("backups.nowhere");
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('"backups.nowhere" has no en text'));
    expect(warn).not.toHaveBeenCalledWith(expect.stringContaining("asked for before"));
  });

  it("says once, in development, which key asked and how to declare the namespace", () => {
    cold();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const loose = translate as (locale: "en", key: string) => string;
    loose("en", "cron.area");
    loose("en", "cron.title");
    const unloaded = warn.mock.calls.filter(([message]) => typeof message === "string" && message.includes("asked for before"));
    // One for the namespace, whatever the number of keys that missed it.
    expect(unloaded).toHaveLength(1);
    expect(String(unloaded[0]?.[0])).toContain('"cron.area"');
    expect(String(unloaded[0]?.[0])).toContain("npm run i18n:routes");
  });

  it("never asks for the chrome, which is bundled", () => {
    const { misses } = cold();
    for (const namespace of CHROME) expect(hasNamespace("en", namespace)).toBe(true);
    translate("en", "time.justNow");
    translate("en", "nav.apps.label");
    expect(misses).toEqual([]);
  });
});

describe("a component that drew the stand-in", () => {
  function Probe({ onRender }: { onRender?: () => void }) {
    const t = useT();
    onRender?.();
    return <p>{t("backups.area")}</p>;
  }

  it("is drawn again with the text when the namespace arrives", async () => {
    cold();
    render(<Probe />);
    expect(screen.getByText("backups.area")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(en.backups.area)).toBeInTheDocument();
    });
  });

  it("is drawn again in Spanish, from English to Spanish, after it had English", async () => {
    cold();
    await loadNamespaces("en", ["backups"]);
    await act(() => setLocale("es"));
    // setLocale brought Spanish up to English's namespaces; forget it again to meet the gap.
    dropNamespaces("es", CHROME);
    render(<Probe />);
    expect(screen.getByText(en.backups.area)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(es.backups.area)).toBeInTheDocument();
    });
  });

  it("is not drawn again for namespaces nobody was waiting for, so the idle preload renders nothing", async () => {
    cold();
    await loadNamespaces("en", ["backups"]);
    let renders = 0;
    render(
      <Probe
        onRender={() => {
          renders += 1;
        }}
      />,
    );
    const before = renders;
    await act(async () => {
      await loadNamespaces("en", ["databases", "domains", "fleet", "settings"]);
    });
    expect(renders).toBe(before);
  });

  it("renders rich text with the stand-in too", async () => {
    cold();
    function Rich() {
      const t = useT();
      return <p>{t.rich("backups.area", {})}</p>;
    }
    render(<Rich />);
    expect(screen.getByText("backups.area")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(en.backups.area)).toBeInTheDocument();
    });
  });
});
