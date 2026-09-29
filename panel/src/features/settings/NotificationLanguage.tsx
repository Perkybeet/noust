import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";
import { useQuery } from "@tanstack/react-query";

import { patchConfig } from "../../api/queries/config";
import type { ConsoleConfig } from "../../api/queries/config";
import { LOCALE_CHOICES } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { QueryState } from "../../components/page/QueryState";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { useRefreshConfig } from "./channelParts";
import { readNotificationSettings } from "./notifications";
import { configGetCommand, configSetCommand } from "./shell";
import { SettingsFormCard, SettingsFormSkeleton, SettingsSection } from "./SettingsForm";
import { useSettingsForm } from "./useSettingsForm";

type ConfigQuery = ReturnType<typeof useQuery<ConsoleConfig>>;

/**
 * English or Español, each written and pronounced in its own language and marked with `lang`,
 * the same autonyms and choices as the browser-only language switch (app/LanguageSwitch.tsx).
 * A plain `Select` cannot mark one option `lang="es"` while its popup speaks whatever language
 * the console is in, so this uses Base UI's radio primitives directly, styled like the
 * console's other segmented choices (SMTP's Encryption).
 */
function LanguagePicker({
  label,
  value,
  onValueChange,
  disabled,
}: {
  label: string;
  value: Locale;
  onValueChange: (locale: Locale) => void;
  disabled: boolean;
}) {
  return (
    <RadioGroup<Locale>
      aria-label={label}
      value={value}
      onValueChange={(next: Locale) => {
        onValueChange(next);
      }}
      className="inline-flex shrink-0 items-center gap-0.5 rounded-control border border-border bg-bg-sunken p-0.5"
    >
      {LOCALE_CHOICES.map((choice) => (
        <Radio.Root
          key={choice.value}
          value={choice.value}
          lang={choice.value}
          disabled={disabled}
          className={cx(
            "inline-flex h-7 min-w-24 cursor-pointer items-center justify-center rounded-[4px] px-3 text-13 font-medium text-fg-muted select-none",
            "transition-[background-color,color] duration-(--duration-fast) ease-out hover:text-fg",
            "data-checked:bg-surface data-checked:text-fg data-checked:shadow-raised",
            "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
            "data-disabled:cursor-not-allowed data-disabled:opacity-60",
          )}
        >
          {choice.label}
        </Radio.Root>
      ))}
    </RadioGroup>
  );
}

/**
 * Notifications > Language: the language Noust writes its own notification text in - titles,
 * event descriptions, the words around a link. Never the tools it wraps: what systemd,
 * certbot or git print is shown as they wrote it, in every language. Bound to
 * `notifications.language` through the same generic config API as the console's address,
 * below (`PATCH /api/config`).
 */
export function NotificationLanguageSection({ query }: { query: ConfigQuery }) {
  const t = useT();
  const refresh = useRefreshConfig();
  const form = useSettingsForm({
    server: query.data === undefined ? undefined : { language: readNotificationSettings(query.data.config).language },
    names: ["language"],
    soleField: "language",
    save: async ({ language }) => {
      await patchConfig("notifications.language", language);
      await refresh();
      toast.success(t("settings.notifications.language.saved"));
    },
  });
  const value: Locale = form.values?.language === "es" ? "es" : "en";
  return (
    <SettingsSection
      title={t("settings.notifications.language.title")}
      description={t("settings.notifications.language.description")}
      commands={form.dirty ? [configSetCommand("notifications.language", value)] : [configGetCommand("notifications.language")]}
    >
      <QueryState query={query} label={t("settings.notifications.language.loadingLabel")} skeleton={<SettingsFormSkeleton fields={[{}]} />}>
        {() => (
          <SettingsFormCard
            dirty={form.dirty}
            pending={form.pending}
            formError={form.formError}
            errorTitle={t("settings.notifications.language.errorTitle")}
            onSubmit={form.submit}
            onDiscard={form.discard}
          >
            <div className="flex min-w-0 flex-col gap-1.5">
              <span aria-hidden="true" className="text-13 font-medium text-fg">
                {t("settings.notifications.language.fieldLabel")}
              </span>
              <LanguagePicker
                label={t("settings.notifications.language.fieldLabel")}
                value={value}
                onValueChange={(next) => {
                  form.set("language", next);
                }}
                disabled={form.pending}
              />
            </div>
          </SettingsFormCard>
        )}
      </QueryState>
    </SettingsSection>
  );
}
