import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { ElevationCancelledError, isApiError, request } from "../../../api/client";
import type { PublicOpened, PublicPlan } from "../../../api/queries/publicAccess";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Stepper } from "../../../components/page/Stepper";
import { WizardActions } from "../../../components/page/Wizard";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { parseAddresses } from "../../../lib/addresses";
import { engineName } from "../engines";
import { ACCESS_PROFILES, MAX_SOURCES, canRequireTls, initialForm, requestOf, validPort } from "./publicAccess";
import type { AccessProfile, PublicForm, TlsMode } from "./publicAccess";

type Step = "choose" | "review";

type FormErrors = Partial<Record<"port" | "sources" | "tls" | "bind" | "profile", string>>;

const FIELDS = ["port", "sources", "tls", "bind", "profile"] as const;

/** The firewall line of a plan, in words: what Noust will tell it, or that there is nothing to tell. */
function firewallWords(t: T, plan: PublicPlan): string {
  if (!plan.firewall.manages) return t("publicAccess.dialog.firewallNone");
  return plan.firewall.active
    ? t("publicAccess.dialog.firewallManaged", { backend: plan.firewall.backend })
    : t("publicAccess.dialog.firewallInactive", { backend: plan.firewall.backend });
}

export interface OpenPublicDialogProps {
  engine: string;
  database: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the answer, which carries the account's password this once. */
  onOpened: (opened: PublicOpened) => void;
}

/**
 * Making a database public, as two steps and one decision: what to open (a port, who may reach
 * it, what the account can do, TLS), then what Noust will do and what the operator accepts by
 * going ahead, in the server's own words. Nothing is changed before the last button, which asks
 * "Confirm it's you" like every action that reaches beyond this server.
 */
export function OpenPublicDialog({ engine, database, open, onOpenChange, onOpened }: OpenPublicDialogProps) {
  const t = useT();
  const [step, setStep] = useState<Step>("choose");
  const [form, setForm] = useState<PublicForm>(() => initialForm(engine));
  const [errors, setErrors] = useState<FormErrors>({});
  const [plan, setPlan] = useState<PublicPlan | null>(null);
  const [accepted, setAccepted] = useState(false);
  const portRef = useRef<HTMLInputElement>(null);
  const requireTls = canRequireTls(engine);

  const review = useMutation({
    mutationFn: () => request("post", "/api/databases/public/plan", { body: { ...requestOf(engine, database, form), confirm: false } }),
    onSuccess: (answer) => {
      setPlan(answer);
      setAccepted(false);
      setStep("review");
    },
    onError: (error) => {
      // A refusal the form can point at (a TLS the engine cannot give, a port taken) goes under
      // its field; anything else is shown whole where the operator is looking.
      if (isApiError(error) && error.fields !== null) {
        const found: FormErrors = {};
        for (const field of FIELDS) {
          const message = error.fields[field];
          if (message !== undefined) found[field] = message;
        }
        if (Object.keys(found).length > 0) setErrors(found);
      }
    },
  });

  const publish = useMutation({
    mutationFn: () => request("post", "/api/databases/public", { body: { ...requestOf(engine, database, form), confirm: true } }),
    onSuccess: (opened) => {
      onOpened(opened);
      onOpenChange(false);
    },
    onError: (error) => {
      // Closing "Confirm it's you" changed nothing: the review stays as it was.
      if (error instanceof ElevationCancelledError) publish.reset();
    },
  });

  const set = (patch: Partial<PublicForm>): void => {
    setForm((previous) => ({ ...previous, ...patch }));
    setErrors({});
    review.reset();
  };

  const validate = (): boolean => {
    const found: FormErrors = {};
    if (!validPort(form.port)) found.port = t("publicAccess.dialog.portInvalid");
    const parsed = parseAddresses(form.sources);
    if (parsed.invalid.length > 0) found.sources = t("publicAccess.dialog.sourcesInvalid", { entries: parsed.invalid.join(", ") });
    else if (parsed.entries.length > MAX_SOURCES) found.sources = t("publicAccess.dialog.sourcesTooMany", { max: MAX_SOURCES });
    setErrors(found);
    return Object.keys(found).length === 0;
  };

  const onClose = (next: boolean): void => {
    if (next || publish.isPending || review.isPending) return;
    onOpenChange(false);
    setStep("choose");
    setForm(initialForm(engine));
    setErrors({});
    setPlan(null);
    setAccepted(false);
    review.reset();
    publish.reset();
  };

  const go = (): void => {
    if (step === "choose") {
      if (validate()) review.mutate();
      else portRef.current?.focus();
      return;
    }
    publish.mutate();
  };

  const steps = [
    { id: "choose", label: t("publicAccess.dialog.steps.choose") },
    { id: "review", label: t("publicAccess.dialog.steps.review") },
  ];
  const name = engineName(engine);

  return (
    <Dialog
      open={open}
      onOpenChange={onClose}
      size="lg"
      initialFocus={portRef}
      title={t("publicAccess.dialog.title", { name: database })}
      description={t("publicAccess.dialog.description")}
      footer={
        <WizardActions
          className="w-full"
          back={{
            label: step === "choose" ? t("publicAccess.dialog.cancel") : t("publicAccess.dialog.back"),
            onClick: () => {
              if (publish.isPending || review.isPending) return;
              if (step === "choose") onClose(false);
              else {
                publish.reset();
                setStep("choose");
              }
            },
          }}
          next={{
            label: step === "choose" ? t("publicAccess.dialog.review") : t("publicAccess.dialog.submit"),
            loading: review.isPending || publish.isPending,
            onClick: go,
          }}
          {...(step === "review" && !accepted ? { missing: t("publicAccess.dialog.acceptRequired") } : {})}
        />
      }
    >
      <div className="flex min-w-0 flex-col gap-5">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {step === "choose" ? (
          <form
            noValidate
            className="flex min-w-0 flex-col gap-5"
            onSubmit={(event) => {
              event.preventDefault();
              go();
            }}
          >
            <Field label={t("publicAccess.dialog.port")} description={t("publicAccess.dialog.portHelp")} error={errors.port} optional>
              <Input ref={portRef} mono inputMode="numeric" value={form.port} onValueChange={(port: string) => set({ port })} placeholder="5433" className="w-40" autoComplete="off" />
            </Field>
            <Field label={t("publicAccess.dialog.sources")} description={t("publicAccess.dialog.sourcesHelp")} error={errors.sources} optional>
              <Textarea mono rows={2} value={form.sources} onChange={(event) => set({ sources: event.target.value })} placeholder="203.0.113.7" autoComplete="off" />
            </Field>
            <ChoiceCards<AccessProfile>
              legend={t("publicAccess.dialog.profile")}
              value={form.profile}
              onValueChange={(profile) => set({ profile })}
              options={ACCESS_PROFILES.map((profile) => ({
                value: profile,
                label: t(`publicAccess.profile.${profile}`),
                description: t(`publicAccess.profile.${profile}Help`),
              }))}
            />
            {errors.profile !== undefined ? <p className="text-13 text-fail">{errors.profile}</p> : null}
            <ChoiceCards<TlsMode>
              legend={t("publicAccess.dialog.tlsLabel")}
              value={form.tls}
              onValueChange={(tls) => set({ tls })}
              options={[
                { value: "required", label: t("publicAccess.dialog.tlsRequired"), description: t("publicAccess.dialog.tlsRequiredHelp") },
                { value: "optional", label: t("publicAccess.dialog.tlsOptional"), description: t("publicAccess.dialog.tlsOptionalHelp") },
              ]}
            />
            {errors.tls !== undefined ? (
              <Notice tone="warning" title={t("publicAccess.dialog.tlsLabel")}>
                {errors.tls}
              </Notice>
            ) : null}
            {!requireTls ? <Notice tone="warning">{t("publicAccess.dialog.tlsCannotRequire", { engine: name })}</Notice> : null}
            <Disclosure label={t("publicAccess.dialog.moreOptions")} defaultOpen={form.bind !== ""}>
              <Field label={t("publicAccess.dialog.bind")} description={t("publicAccess.dialog.bindHelp")} error={errors.bind} optional>
                <Input mono value={form.bind} onValueChange={(bind: string) => set({ bind })} placeholder="0.0.0.0" className="w-56" autoComplete="off" spellCheck={false} />
              </Field>
            </Disclosure>
            {review.isError && Object.keys(errors).length === 0 ? <ErrorBlock live compact error={review.error} title={t("publicAccess.dialog.planFailed")} /> : null}
          </form>
        ) : plan !== null ? (
          <div className="flex min-w-0 flex-col gap-5">
            <div className="flex flex-col gap-2">
              <p className="text-13 font-medium text-fg">{t("publicAccess.dialog.reviewTitle")}</p>
              <KeyValueList
                items={[
                  { label: t("publicAccess.dialog.reviewFacts.port"), value: String(plan.port) },
                  { label: t("publicAccess.dialog.reviewFacts.listen"), value: plan.bind_address },
                  { label: t("publicAccess.dialog.reviewFacts.forwards"), value: plan.target },
                  { label: t("publicAccess.facts.from"), value: plan.sources.length === 0 ? t("publicAccess.anyone") : plan.sources.join(", "), mono: plan.sources.length > 0 },
                  { label: t("publicAccess.dialog.reviewFacts.account"), value: `${plan.remote_user} · ${t(`publicAccess.profile.${plan.profile as AccessProfile}`)}`, copy: plan.remote_user },
                  { label: t("publicAccess.facts.tls"), value: plan.tls === "required" ? t("publicAccess.tls.required") : t("publicAccess.tls.optional"), mono: false, copy: false },
                  { label: t("publicAccess.dialog.reviewFacts.unit"), value: plan.unit },
                  { label: t("publicAccess.dialog.reviewFacts.firewall"), value: firewallWords(t, plan), mono: false, copy: false },
                ]}
              />
            </div>
            {plan.warnings.length > 0 ? (
              <Notice tone="warning" title={t("publicAccess.dialog.warningsTitle")}>
                <ul className="flex flex-col gap-2" translate="no">
                  {plan.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </Notice>
            ) : null}
            <Checkbox
              checked={accepted}
              onCheckedChange={setAccepted}
              label={t("publicAccess.dialog.accept")}
            />
            {publish.isError && !(publish.error instanceof ElevationCancelledError) ? (
              <ErrorBlock live compact error={publish.error} title={t("publicAccess.dialog.submitFailed")} />
            ) : null}
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
