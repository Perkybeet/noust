import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { BookOpen, Keyboard, KeyRound, LogOut } from "lucide-react";
import type { ReactNode } from "react";

import { sessionQuery } from "../api/queries/auth";
import { SegmentedControl } from "../components/page/SegmentedControl";
import { Button, buttonClassName } from "../components/ui/Button";
import { ExternalLink } from "../components/ui/ExternalLink";
import { Kbd } from "../components/ui/Kbd";
import { Mono } from "../components/ui/Mono";
import { OBJECT_ICONS } from "../components/ui/objectIcons";
import { useSignOut } from "../features/auth/useSignOut";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { helpFor, helpUrl } from "./help";
import { LanguageSwitch } from "./LanguageSwitch";
import { setPageWidth, usePreferences } from "./preferences";
import type { PageWidth } from "./preferences";
import { ThemeSwitch } from "./ThemeSwitch";
import { useTheme } from "./theme";

/** What the session's panel says under its title: who is signed in, and on which server. */
export function useSessionDescription(): ReactNode {
  const t = useT();
  const { data: session } = useQuery(sessionQuery());
  const username = session?.account?.username;
  if (session?.hostname === undefined) return undefined;
  return username !== undefined
    ? t.rich("shell.session.signedInAs", { user: <Mono key="user">{username}</Mono>, hostname: <Mono key="host">{session.hostname}</Mono> })
    : t.rich("shell.session.signedInWithToken", { hostname: <Mono key="host">{session.hostname}</Mono> });
}

/**
 * The person's own session: the account, the theme and the language, the width of the page, and
 * signing out. It is the body of a popover at the top bar on a desktop and of a sheet from "More" on a
 * phone, so the two are never two lists. The documentation and the shortcuts are the "?" of the top bar's
 * (`features/report/HelpMenu`) and are not said again here; a phone has no "?", so its sheet asks for them
 * (`withHelp`) and they are in one place there too.
 */
export function SessionMenu({ onClose, onOpenShortcuts, withHelp = false }: { onClose: () => void; onOpenShortcuts: () => void; withHelp?: boolean }) {
  const t = useT();
  const [theme, chooseTheme] = useTheme();
  const { data: session } = useQuery(sessionQuery());
  const { signOut, pending: signingOut } = useSignOut();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const { width } = usePreferences();

  return (
    <div className="flex flex-col gap-4">
      {/* Who is signed in and what is theirs: the account is two clicks from anywhere, and so is turning on the second factor. */}
      <div className="-mx-2 flex flex-col">
        <Link to="/account" onClick={onClose} className={cx(buttonClassName("ghost"), "justify-start")}>
          <OBJECT_ICONS.account aria-hidden="true" className="size-icon-md shrink-0" />
          {t("shell.session.account")}
        </Link>
        <Link to="/account/security" onClick={onClose} className={cx(buttonClassName("ghost"), "justify-start")}>
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
        {withHelp ? (
          <>
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
                onClose();
                onOpenShortcuts();
              }}
            >
              {t("shell.session.keyboardShortcuts")}
            </Button>
          </>
        ) : null}
        <Button variant="ghost" icon={<LogOut aria-hidden="true" />} className="justify-start" loading={signingOut} onClick={() => void signOut()}>
          {t("shell.session.signOut")}
        </Button>
      </div>
      {session?.version !== undefined ? <p className="text-12 text-fg-muted">{t.rich("shell.version", { version: <Mono tone="muted">{session.version}</Mono> })}</p> : null}
    </div>
  );
}
