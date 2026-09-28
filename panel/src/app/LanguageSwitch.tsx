import { toast } from "../components/ui/toast";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { LOCALE_CHOICES, useLocale } from "./locale";

export interface LanguageSwitchProps {
  className?: string;
}

/**
 * English or Español, as pressed-or-not buttons like the theme switch. Each is written in its
 * own language and marked with `lang`, so it is recognisable and pronounced correctly whatever
 * the console currently speaks. The switch applies at once: nothing to save.
 */
export function LanguageSwitch({ className }: LanguageSwitchProps) {
  const t = useT();
  const [locale, setLocale] = useLocale();
  return (
    <div
      role="group"
      aria-label={t("language.label")}
      className={cx("flex rounded-control border border-border bg-bg-sunken p-0.5", className)}
    >
      {LOCALE_CHOICES.map(({ value, label }) => (
        <button
          key={value}
          type="button"
          lang={value}
          aria-pressed={locale === value}
          onClick={() => {
            setLocale(value).catch((error: unknown) => {
              console.error(error);
              toast.error(t("language.loadFailed"), { description: t("language.loadFailedHint") });
            });
          }}
          className={cx(
            "flex h-7 flex-1 cursor-pointer items-center justify-center rounded-[4px] px-2.5 text-12 font-medium text-fg-muted",
            "hover:text-fg focus-visible:outline-2 focus-visible:outline-focus",
            "aria-pressed:bg-surface aria-pressed:text-fg aria-pressed:shadow-raised",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
