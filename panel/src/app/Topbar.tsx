import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { BookOpen, Keyboard, KeyRound, LogOut, Moon, Search, Sun } from "lucide-react";
import { useState } from "react";
import type { Ref } from "react";

import { sessionQuery } from "../api/queries/auth";
import { Logo } from "../components/brand/Logo";
import { SegmentedControl } from "../components/page/SegmentedControl";
import { Button, buttonClassName } from "../components/ui/Button";
import { ExternalLink } from "../components/ui/ExternalLink";
import { IconButton } from "../components/ui/IconButton";
import { Kbd } from "../components/ui/Kbd";
import { Mono } from "../components/ui/Mono";
import { Popover } from "../components/ui/Popover";
import { useMediaQuery } from "../components/ui/useMediaQuery";
import { useSignOut } from "../features/auth/useSignOut";
import { InboxButton } from "../features/inbox/InboxButton";
import { TeamSwitcher } from "../features/teams/TeamSwitcher";
import { ScopeSelector } from "../nodes/ScopeSelector";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { Crumbs } from "./frame/Crumbs";
import { PresentationSwitch } from "./frame/PresentationSwitch";
import { helpFor, helpUrl } from "./help";
import { LanguageSwitch } from "./LanguageSwitch";
import { MachineStrip } from "./MachineStrip";
import { useNavLayout } from "./navLayout";
import { setPageWidth, usePreferences } from "./preferences";
import type { PageWidth } from "./preferences";
import { modKeyLabel } from "./shortcuts";
import { ThemeSwitch } from "./ThemeSwitch";
import { setTheme, useTheme } from "./theme";
import { OBJECT_ICONS } from "../components/ui/objectIcons";

export interface TopbarProps {
  onOpenPalette: () => void;
  onOpenShortcuts: () => void;
  /** Receives the search trigger, so the palette can hand focus back to it. */
  searchTriggerRef?: Ref<HTMLButtonElement>;
}

/** The person's initials for the button that opens their session: the first letters of their name, or none when signed in with the token. */
function initialsOf(username: string | undefined): string | null {
  if (username === undefined) return null;
  const words = username.split(/[^\p{L}\p{N}]+/u).filter((part) => part !== "");
  const first = words[0]?.[0];
  if (first === undefined) return null;
  return (words.length > 1 ? `${first}${words[1]?.[0] ?? ""}` : (words[0]?.slice(0, 2) ?? first)).toUpperCase();
}

function SessionPanel({ onOpenShortcuts }: { onOpenShortcuts: () => void }) {
  const t = useT();
  const [theme, chooseTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const { data: session } = useQuery(sessionQuery());
  const { signOut, pending: signingOut } = useSignOut();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const layout = useNavLayout();
  const { presentation, width } = usePreferences();

  const username = session?.account?.username;
  const initials = initialsOf(username);
  const close = (): void => setOpen(false);

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      align="end"
      trigger={
        <IconButton
          label={t("shell.session.label")}
          variant="secondary"
          icon={
            initials !== null ? (
              <span aria-hidden="true" className="text-12 leading-none font-semibold text-accent-fg">
                {initials}
              </span>
            ) : (
              <OBJECT_ICONS.account />
            )
          }
          tooltip={false}
        />
      }
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
          <ThemeSwitch value={theme} onChange={chooseTheme} />
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-12 font-medium text-fg-muted">{t("language.label")}</span>
          <LanguageSwitch />
        </div>
        {/* What the frame lets a person choose (app/preferences.ts): kept in this browser, like the theme. */}
        {layout.tier === "narrow" || layout.tier === "phone" ? null : (
          <div className="flex flex-col gap-1.5">
            <span className="text-12 font-medium text-fg-muted">{t("nav.presentation.label")}</span>
            <PresentationSwitch presentation={presentation} />
          </div>
        )}
        <div className="flex flex-col gap-1.5">
          <span className="text-12 font-medium text-fg-muted">{t("shell.session.pageWidth")}</span>
          <SegmentedControl<PageWidth>
            label={t("shell.session.pageWidth")}
            value={width}
            onValueChange={setPageWidth}
            options={[
              { value: "comfortable", label: t("shell.session.widthComfortable") },
              { value: "full", label: t("shell.session.widthFull") },
            ]}
          />
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
        {session?.version !== undefined ? <p className="text-12 text-fg-muted">{t.rich("shell.version", { version: <Mono tone="muted">{session.version}</Mono> })}</p> : null}
      </div>
    </Popover>
  );
}

/** One press between light and dark, for the person who does not want the system's: the full choice (with "System") is in the session's panel. */
function ThemeToggle() {
  const t = useT();
  const [theme] = useTheme();
  const systemDark = useMediaQuery("(prefers-color-scheme: dark)");
  const dark = theme === "dark" || (theme === "system" && systemDark);
  return (
    <IconButton
      label={dark ? t("shell.commands.switchToLightTheme") : t("shell.commands.switchToDarkTheme")}
      variant="secondary"
      icon={dark ? <Sun /> : <Moon />}
      onClick={() => {
        setTheme(dark ? "light" : "dark");
      }}
      tooltip={false}
      className="max-sm:hidden"
    />
  );
}

/**
 * The bar over every page: where the page is (the breadcrumbs, generated from the register), the
 * machine's readout, and what is always at hand at the right (search, the theme, what needs you, the
 * session). It is the page's own ground, not a bar of its own, so the page reads as one surface from the
 * top; the navigation is at the side. On a phone the areas are at the foot and the logo is here.
 */
export function Topbar({ onOpenPalette, onOpenShortcuts, searchTriggerRef }: TopbarProps) {
  const t = useT();
  const mod = modKeyLabel();
  const { phone } = useNavLayout();
  return (
    <header className="sticky top-0 z-sticky flex h-14 shrink-0 items-center gap-3 bg-bg px-4 sm:px-6 lg:px-8">
      {phone ? (
        <Link to="/" aria-label={t("nav.overview.label")} className="-ml-1 flex shrink-0 items-center rounded-control p-1">
          {/* The mark alone: the machine's name beside it is what a phone has no room to lose. */}
          <Logo variant="icon" height={26} decorative />
        </Link>
      ) : null}

      <ScopeSelector placement="topbar" />
      <TeamSwitcher />
      {/* On a phone the trail is a row of its own at the top of the page (app/Shell.tsx): there is no room for it here. */}
      {phone ? null : <Crumbs className="shrink" />}

      <MachineStrip className="flex-1" />

      <div className="flex shrink-0 items-center gap-1.5">
        <button
          ref={searchTriggerRef}
          type="button"
          onClick={onOpenPalette}
          aria-haspopup="dialog"
          aria-keyshortcuts="Control+K Meta+K"
          className="flex h-control-md w-52 cursor-pointer items-center gap-2 rounded-control bg-bg-sunken pr-1.5 pl-2.5 text-13 text-fg-muted elev-carved transition-colors duration-(--duration-fast) ease-out hover:bg-bg-sunken-deep hover:text-fg max-md:hidden lg:w-44 xl:w-60"
        >
          <Search aria-hidden="true" className="size-4 shrink-0" />
          <span className="flex-1 text-left">{t("shell.search.label")}</span>
          <span aria-hidden="true" className="flex gap-0.5">
            <Kbd>{mod}</Kbd>
            <Kbd>K</Kbd>
          </span>
        </button>
        <IconButton label={t("shell.search.label")} icon={<Search />} onClick={onOpenPalette} tooltip={false} className="md:hidden" />
        <ThemeToggle />
        <InboxButton />
        <SessionPanel onOpenShortcuts={onOpenShortcuts} />
      </div>
    </header>
  );
}
