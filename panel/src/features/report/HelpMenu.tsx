import { useNavigate, useRouterState } from "@tanstack/react-router";
import { BookOpen, Bug, CircleHelp, Keyboard } from "lucide-react";

import { helpFor, helpUrl } from "../../app/help";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { ICONS } from "../../components/ui/icons";
import { useT } from "../../i18n";
import { openReport } from "./store";

/**
 * The "?" of the top bar: the documentation of the page on screen, the shortcuts, the way to tell the maintainer that
 * something is wrong, and what this installation is. What explains the console and what reports it are one menu,
 * so a person who is lost or stuck looks in one place.
 */
export function HelpMenu({ onOpenShortcuts }: { onOpenShortcuts: () => void }) {
  const t = useT();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  return (
    <Menu align="end" trigger={<IconButton label={t("shell.help.label")} icon={<CircleHelp />} tooltip={false} />}>
      <MenuItem
        icon={<BookOpen />}
        onClick={() => {
          window.open(helpUrl(helpFor(pathname)), "_blank", "noopener,noreferrer");
        }}
      >
        {t("shell.session.documentation")}
      </MenuItem>
      <MenuItem icon={<Keyboard />} shortcut={["?"]} onClick={onOpenShortcuts}>
        {t("shell.session.keyboardShortcuts")}
      </MenuItem>
      <MenuSeparator />
      <MenuItem
        icon={<Bug />}
        onClick={() => {
          void openReport({ from: "help", pathname });
        }}
      >
        {t("shell.help.report")}
      </MenuItem>
      <MenuItem
        icon={<ICONS.info />}
        onClick={() => {
          void navigate({ to: "/settings/about" });
        }}
      >
        {t("nav.settingsTabs.about.label")}
      </MenuItem>
    </Menu>
  );
}
