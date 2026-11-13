import { Subsection } from "../../../components/page/Subsection";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { LIMIT_KEYS, hasPrice } from "./model";
import type { Draft, LimitKey } from "./model";

const LABELS: Record<LimitKey, { label: "steps" | "tokens" | "spendConversation" | "spendMonth" | "actions"; money: boolean }> = {
  steps_per_turn: { label: "steps", money: false },
  input_tokens_per_conversation: { label: "tokens", money: false },
  spend_per_conversation: { label: "spendConversation", money: true },
  spend_per_month: { label: "spendMonth", money: true },
  actions_per_hour: { label: "actions", money: false },
};

export interface LimitsFieldsProps {
  draft: Draft;
  onChange: (next: Draft) => void;
  /** The model the prices are for, said in the note. */
  model: string;
}

/**
 * The limits and the prices of the model, as fields: five limits that are always numbers (none can be taken away, only changed)
 * and the model's price per million tokens in the currency the person says. A model with no price has no limit of spending, and
 * the form says so beside the prices: nothing is estimated for it and no price is invented.
 */
export function LimitsFields({ draft, onChange, model }: LimitsFieldsProps) {
  const t = useT();
  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-5 sm:grid-cols-2">
        {LIMIT_KEYS.map((key) => {
          const { label, money } = LABELS[key];
          return (
            <Field key={key} label={t(`assistantSettings.limits.${label}`)} description={t(`assistantSettings.limits.${label}Help`)}>
              <Input
                inputMode="decimal"
                mono
                value={draft.limits[key]}
                {...(key === "input_tokens_per_conversation" ? { suffix: t("assistantSettings.limits.tokensSuffix") } : money ? { suffix: draft.currency } : {})}
                onChange={(event) => onChange({ ...draft, limits: { ...draft.limits, [key]: event.target.value } })}
              />
            </Field>
          );
        })}
      </div>
      <Subsection title={t("assistantSettings.limits.prices")} description={<><Mono>{model}</Mono>{". "}{t("assistantSettings.limits.pricesDescription")}</>}>
        <div className="grid gap-5 sm:grid-cols-3">
          <Field label={t("assistantSettings.limits.input")} description={t("assistantSettings.limits.perMillion")}>
            <Input inputMode="decimal" mono value={draft.inputPrice} suffix={draft.currency} onChange={(event) => onChange({ ...draft, inputPrice: event.target.value })} />
          </Field>
          <Field label={t("assistantSettings.limits.output")} description={t("assistantSettings.limits.perMillion")}>
            <Input inputMode="decimal" mono value={draft.outputPrice} suffix={draft.currency} onChange={(event) => onChange({ ...draft, outputPrice: event.target.value })} />
          </Field>
          <Field label={t("assistantSettings.limits.currency")}>
            <Input mono value={draft.currency} maxLength={8} onChange={(event) => onChange({ ...draft, currency: event.target.value })} />
          </Field>
        </div>
        <InlineMessage tone={hasPrice(draft) ? "info" : "warning"}>{hasPrice(draft) ? t("assistantSettings.limits.pricesNote") : t("assistantSettings.limits.noPrice")}</InlineMessage>
      </Subsection>
    </div>
  );
}
