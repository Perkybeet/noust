import { Monitor, Moon, Sun } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { SlidingIndicator } from "../components/ui/SlidingIndicator";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { THEME_CHOICES } from "./theme";
import type { ThemeChoice } from "./theme";

const ICONS: Record<ThemeChoice, LucideIcon> = { system: Monitor, light: Sun, dark: Moon };

export interface ThemeSwitchProps {
  value: ThemeChoice;
  onChange: (theme: ThemeChoice) => void;
  /** Hide the words on narrow layouts; the icons and accessible names remain. */
  compact?: boolean;
  className?: string;
}

/** System, light or dark, as three pressed-or-not buttons. */
export function ThemeSwitch({ value, onChange, compact = false, className }: ThemeSwitchProps) {
  const t = useT();
  return (
    <div
      role="group"
      aria-label={t("shell.session.theme")}
      className={cx("elev-carved relative isolate flex rounded-card bg-bg-sunken p-0.5", className)}
    >
      <SlidingIndicator className="rounded-control bg-surface" />
      {THEME_CHOICES.map(({ value: choice, label }) => {
        const Icon = ICONS[choice];
        return (
          <button
            key={choice}
            type="button"
            aria-pressed={value === choice}
            onClick={() => {
              onChange(choice);
            }}
            className={cx(
              "relative flex h-7 flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-control px-2.5 text-12 font-medium text-fg-muted",
              "hover:text-fg focus-visible:outline-2 focus-visible:outline-focus",
              "aria-pressed:text-fg",
            )}
          >
            <Icon aria-hidden="true" className="size-3.5" />
            <span className={cx(compact && "max-sm:sr-only")}>{t(label)}</span>
          </button>
        );
      })}
    </div>
  );
}
