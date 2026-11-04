import { useQuery } from "@tanstack/react-query";
import { Moon, Search, Sun } from "lucide-react";
import { useState } from "react";
import type { Ref } from "react";

import { sessionQuery } from "../api/queries/auth";
import { IconButton } from "../components/ui/IconButton";
import { Kbd } from "../components/ui/Kbd";
import { OBJECT_ICONS } from "../components/ui/objectIcons";
import { Popover } from "../components/ui/Popover";
import { useMediaQuery } from "../components/ui/useMediaQuery";
import { InboxButton } from "../features/inbox/InboxButton";
import { TeamSwitcher } from "../features/teams/TeamSwitcher";
import { ScopeSelector } from "../nodes/ScopeSelector";
import { useT } from "../i18n";
import { Crumbs } from "./frame/Crumbs";
import { MachineStrip } from "./MachineStrip";
import { useNavLayout } from "./navLayout";
import { SessionMenu, useSessionDescription } from "./SessionMenu";
import { modKeyLabel } from "./shortcuts";
import { setTheme, useTheme } from "./theme";

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
  const [open, setOpen] = useState(false);
  const { data: session } = useQuery(sessionQuery());
  const description = useSessionDescription();

  const initials = initialsOf(session?.account?.username);

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      align="end"
      trigger={
        <IconButton
          label={initials === null ? t("shell.session.label") : t("shell.session.labelOf", { initials })}
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
      description={description}
      className="w-72"
    >
      <SessionMenu onClose={() => setOpen(false)} onOpenShortcuts={onOpenShortcuts} />
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
      className="max-sm:hidden @max-[64rem]:hidden"
    />
  );
}

/**
 * The bar over every page: where the page is (the breadcrumbs, generated from the register), the
 * machine's readout, and what is always at hand at the right (search, the theme, what needs you, the
 * session). It is the page's own ground, not a bar of its own, so the page reads as one surface from the
 * top; the navigation is at the side. On a phone the areas are at the foot, the server selector is the
 * first thing in the bar with its name whole, and the session is in "More".
 */
export function Topbar({ onOpenPalette, onOpenShortcuts, searchTriggerRef }: TopbarProps) {
  const t = useT();
  const mod = modKeyLabel();
  const { phone } = useNavLayout();
  return (
    // A container of its own: what the bar shows at the right is decided by the room the bar has, which is the page's
    // column (a second column beside the page takes 264px of it), not the window's. The trail comes before the search
    // box and the theme: they give way to it, and both are one press from the session's panel and the palette's key.
    <header className="@container sticky top-0 z-sticky flex h-14 shrink-0 items-center gap-3 bg-bg px-4 sm:px-6 lg:px-8">
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
          className="hidden h-control-md w-44 cursor-pointer items-center gap-2 rounded-control bg-bg-sunken pr-1.5 pl-2.5 text-13 text-fg-muted elev-carved transition-colors duration-(--duration-fast) ease-out hover:bg-bg-sunken-deep hover:text-fg @min-[66rem]:flex @min-[80rem]:w-60"
        >
          <Search aria-hidden="true" className="size-4 shrink-0" />
          <span className="flex-1 text-left">{t("shell.search.label")}</span>
          <span aria-hidden="true" className="flex gap-0.5">
            <Kbd>{mod}</Kbd>
            <Kbd>K</Kbd>
          </span>
        </button>
        <IconButton label={t("shell.search.label")} icon={<Search />} onClick={onOpenPalette} tooltip={false} className="@min-[66rem]:hidden" />
        <ThemeToggle />
        <InboxButton />
        {phone ? null : <SessionPanel onOpenShortcuts={onOpenShortcuts} />}
      </div>
    </header>
  );
}
