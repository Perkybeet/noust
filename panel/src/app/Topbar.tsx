import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { ArrowLeft, CircleUser, Keyboard, LogOut, Menu, Search } from "lucide-react";
import { useState } from "react";
import type { Ref } from "react";

import { sessionQuery } from "../api/queries/auth";
import { Logo } from "../components/brand/Logo";
import { Button } from "../components/ui/Button";
import { IconButton } from "../components/ui/IconButton";
import { Kbd } from "../components/ui/Kbd";
import { Mono } from "../components/ui/Mono";
import { Popover } from "../components/ui/Popover";
import { useSignOut } from "../features/auth/useSignOut";
import { ServerSelector } from "../nodes/ServerSelector";
import { useReturnServer } from "../nodes/servers";
import { useConsoleContext } from "../nodes/useNode";
import { useT } from "../i18n";
import { LanguageSwitch } from "./LanguageSwitch";
import { MachineStrip } from "./MachineStrip";
import { returnTarget } from "./nodeRoute";
import { modKeyLabel } from "./shortcuts";
import { ThemeSwitch } from "./ThemeSwitch";
import { useTheme } from "./theme";

export interface TopbarProps {
  onOpenPalette: () => void;
  onOpenShortcuts: () => void;
  onOpenNav: () => void;
  /** Receives the search trigger, so the palette can hand focus back to it. */
  searchTriggerRef?: Ref<HTMLButtonElement>;
}

function SessionPanel({ onOpenShortcuts }: { onOpenShortcuts: () => void }) {
  const t = useT();
  const [theme, setTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const { data: session } = useQuery(sessionQuery());
  const { signOut, pending: signingOut } = useSignOut();

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      align="end"
      trigger={<IconButton label={t("shell.session.label")} icon={<CircleUser />} tooltip={false} />}
      title={t("shell.session.title")}
      description={
        session?.hostname !== undefined
          ? t.rich("shell.session.signedInTo", { hostname: <Mono>{session.hostname}</Mono> })
          : undefined
      }
      className="w-72"
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <span className="text-12 font-medium text-fg-muted">{t("shell.session.theme")}</span>
          <ThemeSwitch value={theme} onChange={setTheme} />
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-12 font-medium text-fg-muted">{t("language.label")}</span>
          <LanguageSwitch />
        </div>
        <div className="-mx-4 flex flex-col border-t border-border px-2 pt-2">
          <Button
            variant="ghost"
            icon={<Keyboard aria-hidden="true" />}
            trailingIcon={<Kbd className="ml-auto">?</Kbd>}
            className="justify-start"
            onClick={() => {
              setOpen(false);
              onOpenShortcuts();
            }}
          >
            {t("shell.session.keyboardShortcuts")}
          </Button>
          <Button
            variant="ghost"
            icon={<LogOut aria-hidden="true" />}
            className="justify-start"
            loading={signingOut}
            onClick={() => void signOut()}
          >
            {t("shell.session.signOut")}
          </Button>
        </div>
      </div>
    </Popover>
  );
}

/**
 * On the fleet's and the central's pages, the way back to the server the operator came from:
 * the same page there when it has one (its settings from the central's), its overview
 * otherwise. Said in words and in mono, never implied.
 */
function BackToServer() {
  const t = useT();
  const context = useConsoleContext();
  const back = useReturnServer();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (context.kind === "server" || back === null) return null;
  return (
    <Link
      to={returnTarget(pathname)}
      search={{ node: back.node ?? undefined }}
      aria-label={t("fleet.selector.backToLabel", { name: back.name })}
      className="flex h-control-md min-w-0 shrink items-center gap-1.5 rounded-control px-2 text-13 text-fg-muted hover:bg-surface-hover hover:text-fg max-md:hidden"
    >
      <ArrowLeft aria-hidden="true" className="size-icon-sm shrink-0" />
      <span className="min-w-0 truncate">
        {t.rich("fleet.selector.backTo", {
          name: (
            <Mono key="name" tone="default">
              {back.name}
            </Mono>
          ),
        })}
      </span>
    </Link>
  );
}

/** The bar over every page: the machine strip, search, the session, and the menu on phones. */
export function Topbar({ onOpenPalette, onOpenShortcuts, onOpenNav, searchTriggerRef }: TopbarProps) {
  const t = useT();
  const mod = modKeyLabel();
  // Left padding of 26px on wide screens: with the hostname link's own 6px, the hostname
  // starts on the same edge as the page title below it.
  return (
    <header className="sticky top-0 z-sticky flex h-14 shrink-0 items-center gap-3 border-b border-border bg-bg/85 px-4 backdrop-blur-md sm:px-6 lg:pr-6 lg:pl-6.5">
      <Link
        to="/"
        aria-label={t("nav.overview.label")}
        className="-ml-1 flex shrink-0 rounded-control p-1 lg:hidden"
      >
        <Logo variant="icon" height={22} decorative />
      </Link>

      <ServerSelector />
      <BackToServer />

      <MachineStrip className="flex-1" />

      <div className="flex shrink-0 items-center gap-1.5">
        <button
          ref={searchTriggerRef}
          type="button"
          onClick={onOpenPalette}
          aria-haspopup="dialog"
          aria-keyshortcuts="Control+K Meta+K"
          className="flex h-control-md w-52 cursor-pointer items-center gap-2 rounded-control border border-border-strong bg-surface pr-1.5 pl-2.5 text-13 text-fg-muted shadow-raised transition-colors duration-(--duration-fast) ease-out hover:bg-surface-hover hover:text-fg max-md:hidden lg:w-44 xl:w-60"
        >
          <Search aria-hidden="true" className="size-4 shrink-0" />
          <span className="flex-1 text-left">{t("shell.search.label")}</span>
          <span aria-hidden="true" className="flex gap-0.5">
            <Kbd>{mod}</Kbd>
            <Kbd>K</Kbd>
          </span>
        </button>
        <IconButton
          label={t("shell.search.label")}
          icon={<Search />}
          onClick={onOpenPalette}
          tooltip={false}
          className="md:hidden"
        />
        <SessionPanel onOpenShortcuts={onOpenShortcuts} />
        <IconButton label={t("shell.menu.open")} icon={<Menu />} onClick={onOpenNav} tooltip={false} className="lg:hidden" />
      </div>
    </header>
  );
}
