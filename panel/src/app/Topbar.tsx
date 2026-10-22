import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { BookOpen, CircleUser, Keyboard, KeyRound, LogOut, Menu, Search } from "lucide-react";
import { useState } from "react";
import type { Ref } from "react";

import { sessionQuery } from "../api/queries/auth";
import { Logo } from "../components/brand/Logo";
import { Button, buttonClassName } from "../components/ui/Button";
import { ExternalLink } from "../components/ui/ExternalLink";
import { IconButton } from "../components/ui/IconButton";
import { Kbd } from "../components/ui/Kbd";
import { Mono } from "../components/ui/Mono";
import { Popover } from "../components/ui/Popover";
import { LG_UP, useMediaQuery } from "../components/ui/useMediaQuery";
import { useSignOut } from "../features/auth/useSignOut";
import { InboxButton } from "../features/inbox/InboxButton";
import { TeamSwitcher } from "../features/teams/TeamSwitcher";
import { ScopeSelector } from "../nodes/ScopeSelector";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { helpFor, helpUrl } from "./help";
import { LanguageSwitch } from "./LanguageSwitch";
import { MachineStrip } from "./MachineStrip";
import { modKeyLabel } from "./shortcuts";
import { ThemeSwitch } from "./ThemeSwitch";
import { useTheme } from "./theme";
import { OBJECT_ICONS } from "../components/ui/objectIcons";

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
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  const username = session?.account?.username;
  const close = (): void => setOpen(false);

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      align="end"
      trigger={<IconButton label={t("shell.session.label")} icon={<CircleUser />} tooltip={false} />}
      title={t("shell.session.title")}
      description={
        session?.hostname === undefined
          ? undefined
          : username !== undefined
            ? t.rich("shell.session.signedInAs", { user: <Mono key="user">{username}</Mono>, hostname: <Mono key="host">{session.hostname}</Mono> })
            : t.rich("shell.session.signedInWithToken", { hostname: <Mono key="host">{session.hostname}</Mono> })
      }
      className="w-72"
    >
      <div className="flex flex-col gap-4">
        {/* Who is signed in and what is theirs: the account is two clicks from anywhere, and so is turning on the second factor. */}
        <div className="-mx-2 flex flex-col">
          <Link to="/account" onClick={close} className={cx(buttonClassName("ghost"), "justify-start")}>
            <OBJECT_ICONS.account aria-hidden="true" className="size-icon-md shrink-0" />
            {t("shell.session.account")}
          </Link>
          <Link to="/account/security" onClick={close} className={cx(buttonClassName("ghost"), "justify-start")}>
            <KeyRound aria-hidden="true" className="size-icon-md shrink-0" />
            {t("shell.session.signInSecurity")}
          </Link>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-12 font-medium text-fg-muted">{t("shell.session.theme")}</span>
          <ThemeSwitch value={theme} onChange={setTheme} />
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-12 font-medium text-fg-muted">{t("language.label")}</span>
          <LanguageSwitch />
        </div>
        <div className="-mx-4 flex flex-col border-t border-border px-2 pt-2">
          <ExternalLink
            href={helpUrl(helpFor(pathname))}
            button="ghost"
            className="justify-start"
            icon={<BookOpen aria-hidden="true" className="size-icon-md shrink-0" />}
          >
            {t("shell.session.documentation")}
          </ExternalLink>
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

/** The bar over every page: the machine strip, search, the session, and the menu on phones. */
export function Topbar({ onOpenPalette, onOpenShortcuts, onOpenNav, searchTriggerRef }: TopbarProps) {
  const t = useT();
  const mod = modKeyLabel();
  // The selector is the head of the sidebar from `lg`; below it, here: a menu in a drawer is not on screen.
  const wide = useMediaQuery(LG_UP);
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

      {wide ? null : <ScopeSelector placement="topbar" />}
      <TeamSwitcher />

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
        <InboxButton />
        <SessionPanel onOpenShortcuts={onOpenShortcuts} />
        <IconButton label={t("shell.menu.open")} icon={<Menu />} onClick={onOpenNav} tooltip={false} className="lg:hidden" />
      </div>
    </header>
  );
}
