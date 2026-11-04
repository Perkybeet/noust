import { Outlet, useNavigate, useRouter } from "@tanstack/react-router";
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";

import { Drawer } from "../components/ui/Drawer";
import type { AppInfo } from "../features/apps/data";
import { useT } from "../i18n";
import { NodeNotice } from "../nodes/NodeNotice";
import { PageServerContext } from "../nodes/pageServer";
import { useHasFleet } from "../nodes/servers";
import { useServerEvents } from "../realtime/events";
import { cx } from "../lib/cx";
import { columnKindOf } from "./columns";
import { ErrorBoundary } from "./ErrorBoundary";
import { focusPageTitle } from "./focus";
import { BottomBar } from "./frame/BottomBar";
import { NavColumn } from "./frame/NavColumn";
import { SectionSelector } from "./frame/SectionSelector";
import { AreasSheet } from "./frame/AreasSheet";
import { Rail } from "./frame/Rail";
import { usePageServerName } from "./frame/usePageServerName";
import { shortcuts as registeredShortcuts } from "./nav";
import { setHomeCustomising } from "./homeCustomising";
import { useNavLayout } from "./navLayout";
import { useNavScope } from "./useNavScope";
import PaletteHost from "./PaletteHost";
import { usePreferences } from "./preferences";
import { useRenderedPathname } from "./renderedPath";
import { shortcutKey, useKeyboardShortcuts } from "./shortcuts";
import type { KeyBinding } from "./shortcuts";
import { SessionMenu, useSessionDescription } from "./SessionMenu";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { Topbar } from "./Topbar";
import type { AppVerb } from "./verbCommands";

// The dialogs a verb opens (the question before a stop, the choice of version) load when one is first chosen.
const VerbRunner = lazy(() => import("./VerbRunner").then((module) => ({ default: module.VerbRunner })));

/** A verb chosen in the palette; the counter makes choosing the same one twice a second request. */
interface ChosenVerb {
  app: AppInfo;
  verb: AppVerb;
  choice: number;
}

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
      className="fixed top-2 left-2 z-skip -translate-y-[200%] rounded-control bg-surface-raised px-3 py-2 text-13 font-medium text-fg opacity-0 elev-seated focus:translate-y-0 focus:opacity-100"
    >
      {t("shell.skipToContent")}
    </a>
  );
}

const APP_PATH = /^\/apps\/([^/]+)(?:\/|$)/;

/**
 * The frame around every signed-in page: the navigation (the main bar and the context column, or a
 * phone's bar at the foot, by the width of the window), the top bar with the breadcrumbs and the
 * machine strip, the page, and the overlays anyone can summon from anywhere (palette, shortcuts, the
 * phone's sheets). It also opens the live event stream and moves focus to each new page's
 * heading.
 */
export function Shell() {
  const t = useT();
  const navigate = useNavigate();
  const router = useRouter();
  const pathname = useRenderedPathname();

  const [paletteOpen, setPaletteOpen] = useState(false);
  // Its commands are built at the first opening and kept, so a second one is instant and an operator who never opens it pays nothing.
  const [paletteReady, setPaletteReady] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  // The main bar opened over the page, where there is no room for it beside (1024 to 1439 px).
  const [railOpen, setRailOpen] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  // Choosing a page in the menu hands focus to that page's heading, not back to the button.
  const [menuNavigated, setMenuNavigated] = useState(false);

  useServerEvents();
  const pageServer = usePageServerName();
  const hasFleet = useHasFleet();
  const scope = useNavScope();
  const layout = useNavLayout();
  const { width } = usePreferences();
  const [accountOpen, setAccountOpen] = useState(false);
  const sessionDescription = useSessionDescription();
  // Every area has a column. From 1024 px it is the frame's, beside the main bar; on a phone it is the section selector at the top of the page.
  const columnKind = columnKindOf(pathname);

  useEffect(
    () =>
      router.subscribe("onRendered", (event) => {
        if (!event.pathChanged || event.fromLocation === undefined) return;
        setNavOpen(false);
        setRailOpen(false);
        // Customising Home ends when the person leaves it: they never come back to a half-edited page they did not mean to open.
        setHomeCustomising(false);
        focusPageTitle();
      }),
    [router],
  );

  const openPalette = useCallback(() => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setPaletteReady(true);
    setPaletteOpen(true);
  }, []);
  const openShortcuts = useCallback(() => {
    setShortcutsOpen(true);
  }, []);

  // Mod+K works everywhere, fields included: it is a chord, not text.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (shortcutKey(event) !== "k" || !(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
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
    // The letters of the scope on screen: `g a` is the server's Applications on a server and the fleet's on the fleet.
    const navShortcuts = registeredShortcuts(scope).map((entry) => ({ keys: entry.keys, description: t(entry.description), run: go(entry.to) }));
    return [
      ...navShortcuts,
      // The fleet is a scope, not a destination of a server's bar: one key leads to it from a server.
      ...(hasFleet && scope === "server" ? [{ keys: ["g", "f"], description: t("shell.shortcuts.goToFleet"), run: go("/fleet") }] : []),
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
  }, [navigate, router, openPalette, openShortcuts, t, scope, hasFleet]);

  useKeyboardShortcuts(bindings);

  const [chosenVerb, setChosenVerb] = useState<ChosenVerb | null>(null);
  const runVerb = useCallback((verb: AppVerb, app: AppInfo) => {
    setChosenVerb((previous) => ({ app, verb, choice: (previous?.choice ?? 0) + 1 }));
  }, []);

  const closeMenuAfterNavigation = useCallback(() => {
    setMenuNavigated(true);
    setNavOpen(false);
  }, []);

  return (
    <>
      <div className="flex min-h-dvh bg-bg text-fg">
        <SkipLink />
        {layout.phone ? null : <Rail layout={layout} open={railOpen} onOpenChange={setRailOpen} />}
        {columnKind !== null && !layout.phone ? <NavColumn kind={columnKind} /> : null}
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar onOpenPalette={openPalette} onOpenShortcuts={openShortcuts} />
          <main id="main" tabIndex={-1} className="flex-1 outline-none">
            {/* The shell caps the page at --width-page (L-1) unless the person chose the whole width; each
                template sets its own inner grid from the width tokens, so a header and its content always share edges. */}
            <div className={cx("mx-auto w-full px-4 pt-6 sm:px-6 lg:px-8 lg:pt-8", width === "full" ? "max-w-none" : "max-w-page", layout.phone ? "pb-24" : "pb-16")}>
              {/* On a phone the column is a selector that says where the page is and opens its sections as a sheet: never tabs. */}
              {layout.phone && columnKind !== null ? <SectionSelector kind={columnKind} /> : null}
              <PageServerContext value={pageServer}>
                <NodeNotice />
                <ErrorBoundary resetKey={pathname}>
                  <Outlet />
                </ErrorBoundary>
              </PageServerContext>
            </div>
          </main>
        </div>
        {layout.phone ? (
          <BottomBar
            onOpenMore={() => {
              setMenuNavigated(false);
              setNavOpen(true);
            }}
          />
        ) : null}

        {layout.phone ? (
          <>
            <AreasSheet
              open={navOpen}
              onOpenChange={setNavOpen}
              onNavigate={closeMenuAfterNavigation}
              finalFocus={!menuNavigated}
              onOpenAccount={() => {
                setNavOpen(false);
                setAccountOpen(true);
              }}
            />
            <Drawer open={accountOpen} onOpenChange={setAccountOpen} placement="bottom" title={t("shell.session.title")} description={sessionDescription}>
              <div className="px-4">
                <SessionMenu onClose={() => setAccountOpen(false)} onOpenShortcuts={openShortcuts} />
              </div>
            </Drawer>
          </>
        ) : null}

      {paletteReady ? (
        <PaletteHost open={paletteOpen} onOpenChange={setPaletteOpen} onOpenShortcuts={openShortcuts} onVerb={runVerb} returnFocus={() => returnFocus.current} />
      ) : null}
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} shortcuts={bindings} />
      {chosenVerb !== null ? (
        <Suspense fallback={null}>
          <VerbRunner key={chosenVerb.choice} app={chosenVerb.app} verb={chosenVerb.verb} />
        </Suspense>
      ) : null}
      </div>
    </>
  );
}
