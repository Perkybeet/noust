import { Outlet, useNavigate, useRouter, useRouterState } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";

import { Drawer } from "../components/ui/Drawer";
import { useT } from "../i18n";
import { NodeNotice } from "../nodes/NodeNotice";
import { useServerEvents } from "../realtime/events";
import { CommandPalette } from "./CommandPalette";
import { ErrorBoundary } from "./ErrorBoundary";
import { focusPageTitle } from "./focus";
import { NAV_GROUPS, SETTINGS_ITEM } from "./nav";
import { RenameNotice } from "./RenameNotice";
import { useKeyboardShortcuts } from "./shortcuts";
import type { KeyBinding } from "./shortcuts";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { Sidebar, SidebarFooter, SidebarNav } from "./Sidebar";
import { Topbar } from "./Topbar";
import { useConsoleCommands } from "./useConsoleCommands";

/** The first focusable element of every page: straight past the navigation to the content. */
function SkipLink() {
  const t = useT();
  const skip = (event: MouseEvent<HTMLAnchorElement>): void => {
    event.preventDefault();
    const main = document.getElementById("main");
    main?.focus();
    main?.scrollIntoView({ block: "start" });
  };
  return (
    <a
      href="#main"
      onClick={skip}
      className="fixed top-2 left-2 z-[70] -translate-y-[200%] rounded-control bg-surface-raised px-3 py-2 text-13 font-medium text-fg opacity-0 shadow-overlay focus:translate-y-0 focus:opacity-100 focus-visible:outline-2 focus-visible:outline-focus"
    >
      {t("shell.skipToContent")}
    </a>
  );
}

const APP_PATH = /^\/apps\/([^/]+)(?:\/|$)/;

/**
 * The frame around every signed-in page: sidebar, topbar with the machine strip, the page,
 * and the three overlays anyone can summon from anywhere (palette, shortcuts, mobile menu).
 * It also opens the live event stream and moves focus to each new page's heading.
 */
export function Shell() {
  const t = useT();
  const navigate = useNavigate();
  const router = useRouter();
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  // Choosing a page in the menu hands focus to that page's heading, not back to the button.
  const [menuNavigated, setMenuNavigated] = useState(false);

  useServerEvents();

  useEffect(
    () =>
      router.subscribe("onRendered", (event) => {
        if (!event.pathChanged || event.fromLocation === undefined) return;
        setNavOpen(false);
        focusPageTitle();
      }),
    [router],
  );

  const openPalette = useCallback(() => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setPaletteOpen(true);
  }, []);
  const openShortcuts = useCallback(() => {
    setShortcutsOpen(true);
  }, []);

  // Mod+K works everywhere, fields included: it is a chord, not text.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
      event.preventDefault();
      if (paletteOpen) {
        setPaletteOpen(false);
        return;
      }
      // Another dialog owns the keyboard until it is closed.
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return;
      openPalette();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [paletteOpen, openPalette]);

  const bindings = useMemo<KeyBinding[]>(() => {
    const go = (to: string) => () => {
      void navigate({ to });
    };
    const navShortcuts = [...NAV_GROUPS.flat(), SETTINGS_ITEM].flatMap((item) =>
      item.shortcut ? [{ keys: item.shortcut.keys, description: t(item.shortcut.description), run: go(item.to) }] : [],
    );
    return [
      ...navShortcuts,
      {
        keys: ["g", "d"],
        description: t("shell.shortcuts.goToDeployments"),
        run: () => {
          const match = APP_PATH.exec(router.state.location.pathname);
          const domain = match?.[1];
          if (domain !== undefined && domain !== "new") {
            void navigate({ to: "/apps/$domain/deployments", params: { domain: decodeURIComponent(domain) } });
          } else {
            void navigate({ to: "/activity" });
          }
        },
      },
      {
        keys: ["/"],
        description: t("shell.shortcuts.searchPage"),
        run: () => {
          const search = document.querySelector<HTMLElement>("main [data-page-search]");
          if (search) search.focus();
          else openPalette();
        },
      },
      { keys: ["?"], description: t("shell.shortcuts.showShortcuts"), run: openShortcuts },
    ];
  }, [navigate, router, openPalette, openShortcuts, t]);

  useKeyboardShortcuts(bindings);

  const commands = useConsoleCommands(paletteOpen, openShortcuts);

  const closeMenuAfterNavigation = useCallback(() => {
    setMenuNavigated(true);
    setNavOpen(false);
  }, []);

  return (
    <div className="flex min-h-dvh bg-bg text-fg">
      <SkipLink />
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar
          onOpenPalette={openPalette}
          onOpenShortcuts={openShortcuts}
          onOpenNav={() => {
            setMenuNavigated(false);
            setNavOpen(true);
          }}
        />
        <main id="main" tabIndex={-1} className="flex-1 outline-none">
          {/* Data pages use a wide screen: tables and charts earn the room. Prose keeps its
              reading measure (Section and DangerZone cap it in ch), and forms, wizards and
              dialogs keep caps of their own. */}
          <div className="mx-auto w-full max-w-[1600px] px-4 pt-6 pb-16 sm:px-6 lg:px-8 lg:pt-8">
            <RenameNotice />
            <NodeNotice />
            <ErrorBoundary resetKey={pathname}>
              <Outlet />
            </ErrorBoundary>
          </div>
        </main>
      </div>

      <Drawer
        open={navOpen}
        onOpenChange={setNavOpen}
        title={t("shell.menu.title")}
        finalFocus={!menuNavigated}
      >
        <div className="flex min-h-full flex-col justify-between gap-8">
          <SidebarNav onNavigate={closeMenuAfterNavigation} className="-mx-2" />
          <div className="-mx-2 border-t border-border pt-3">
            <SidebarFooter onNavigate={closeMenuAfterNavigation} />
          </div>
        </div>
      </Drawer>

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        commands={commands}
        returnFocus={() => returnFocus.current}
      />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} shortcuts={bindings} />
    </div>
  );
}
