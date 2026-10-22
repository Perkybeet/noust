/**
 * One probe in a drawer: the address and what counts as a good answer, how often it is asked and
 * how many bad ones in a row make it down, and whether it is a component of the status page. A
 * refusal names its field (the server owns what a probe may be) and is shown beside it, verbatim.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { probeKeys, saveProbe } from "../../../api/queries/probes";
import type { Probe } from "../../../api/queries/probes";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { splitErrors } from "../formErrors";
import { FormFailure } from "../SettingsForm";
import { PROBE_FIELDS, PROBE_METHODS, emptyProbe, probeBody, probeFormFrom } from "./data";
import type { ProbeField, ProbeForm, ProbeMethod } from "./data";

export interface ProbeDrawerProps {
  /** The probe being edited; absent for a new one. */
  probe?: Probe;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ProbeDrawer({ probe, open, onOpenChange }: ProbeDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const initial = (): ProbeForm => (probe === undefined ? emptyProbe() : probeFormFrom(probe));
  const [form, setForm] = useState<ProbeForm>(initial);
  const [edited, setEdited] = useState<ReadonlySet<string>>(new Set());
  // What the form could not send because it was left empty, and the boxes to go to when it says so.
  const [missing, setMissing] = useState<ReadonlySet<"name" | "url">>(new Set());
  const nameRef = useRef<HTMLInputElement>(null);
  const urlRef = useRef<HTMLInputElement>(null);

  const set = <K extends keyof ProbeForm>(key: K, value: ProbeForm[K], touched: readonly string[] = [key]): void => {
    setForm((current) => ({ ...current, [key]: value }));
    if (key === "name" || key === "url") setMissing((current) => new Set([...current].filter((field) => field !== key)));
    // A message is about the value that was sent: it goes away when the value is edited.
    setEdited((current) => new Set([...current, ...touched]));
  };

  const save = useMutation({
    mutationFn: () => saveProbe(form.name.trim(), probeBody(form, probe?.enabled ?? true)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: probeKeys.all });
      onOpenChange(false);
    },
    onMutate: () => {
      setEdited(new Set());
    },
  });
  const split = splitErrors(save.error, PROBE_FIELDS);
  const message = (...names: ProbeField[]): string | undefined => {
    if (names.includes("name") && missing.has("name")) return t("probes.form.nameMissing");
    if (names.includes("url") && missing.has("url")) return t("probes.form.urlMissing");
    if (names.some((name) => edited.has(name))) return undefined;
    return names.map((name) => split.fields[name]).find((text) => text !== undefined);
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (save.isPending) return;
    // Pressing it with something left out says what, beside the box, and goes to the first.
    const lacks = new Set<"name" | "url">();
    if (form.name.trim() === "") lacks.add("name");
    if (form.url.trim() === "") lacks.add("url");
    if (lacks.size > 0) {
      setMissing(lacks);
      (lacks.has("name") ? nameRef : urlRef).current?.focus();
      return;
    }
    save.mutate();
  };
  const reset = (): void => {
    setForm(initial());
    setEdited(new Set());
    setMissing(new Set());
    save.reset();
  };

  return (
    <Drawer
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
      title={probe === undefined ? t("probes.form.newTitle") : t("common.words.editNamed", { name: probe.name })}
      description={t("probes.form.description")}
      footer={
        <>
          <Button
            variant="ghost"
            disabled={save.isPending}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("probes.form.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex min-w-0 flex-col gap-5">
        <FormFailure error={split.form} title={t("probes.form.saveErrorTitle")} />
        <Field label={t("common.words.name")} description={t("probes.form.nameDescription")} error={message("name")}>
          <Input
            ref={nameRef}
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="shop"
            // A probe is its name: changing it would be another probe, so an existing one keeps it.
            readOnly={probe !== undefined}
            value={form.name}
            onValueChange={(next: string) => {
              set("name", next);
            }}
          />
        </Field>
        <Field label={t("probes.form.urlLabel")} description={t("probes.form.urlDescription")} error={message("url")}>
          <Input
            ref={urlRef}
            mono
            type="url"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="https://shop.example.com/health"
            value={form.url}
            onValueChange={(next: string) => {
              set("url", next);
            }}
          />
        </Field>
        <div className="grid min-w-0 gap-4 sm:grid-cols-2">
          <Field label={t("probes.form.expectStatusLabel")} description={t("probes.form.expectStatusDescription")} error={message("expect_status")}>
            <Input
              mono
              autoComplete="off"
              spellCheck={false}
              placeholder="200-399"
              value={form.expectStatus}
              onValueChange={(next: string) => {
                set("expectStatus", next, ["expect_status"]);
              }}
            />
          </Field>
          <Field label={t("probes.form.expectTextLabel")} optional description={t("probes.form.expectTextDescription")} error={message("expect_text")}>
            <Input
              autoComplete="off"
              spellCheck={false}
              value={form.expectText}
              onValueChange={(next: string) => {
                set("expectText", next, ["expect_text"]);
              }}
            />
          </Field>
        </div>
        <Checkbox
          label={t("probes.form.publicLabel")}
          description={t("probes.form.publicDescription")}
          checked={form.publicOn}
          onCheckedChange={(next) => {
            set("publicOn", next);
          }}
        />
        {form.publicOn ? (
          <Field label={t("probes.form.titleLabel")} optional description={t("probes.form.titleDescription")} error={message("title")}>
            <Input
              autoComplete="off"
              maxLength={80}
              value={form.title}
              onValueChange={(next: string) => {
                set("title", next);
              }}
            />
          </Field>
        ) : null}
        <Disclosure label={t("probes.form.more")} defaultOpen={probe !== undefined && (probe.method !== "GET" || probe.interval_s !== 60 || probe.timeout_s !== 10 || probe.fail_after !== 3)}>
          <div className="flex min-w-0 flex-col gap-5">
            <Field label={t("probes.form.methodLabel")} nativeLabel={false} description={t("probes.form.methodDescription")} error={message("method")}>
              <Select<ProbeMethod>
                value={form.method}
                onValueChange={(next) => {
                  set("method", next);
                }}
                options={PROBE_METHODS.map((method) => ({ value: method, label: method }))}
              />
            </Field>
            <div className="grid min-w-0 gap-4 sm:grid-cols-3">
              <Field label={t("probes.form.intervalLabel")} description={t("probes.form.intervalDescription")} error={message("interval_s")}>
                <Input
                  mono
                  inputMode="numeric"
                  value={form.interval}
                  onValueChange={(next: string) => {
                    set("interval", next, ["interval_s"]);
                  }}
                />
              </Field>
              <Field label={t("probes.form.timeoutLabel")} description={t("probes.form.timeoutDescription")} error={message("timeout_s")}>
                <Input
                  mono
                  inputMode="numeric"
                  value={form.timeout}
                  onValueChange={(next: string) => {
                    set("timeout", next, ["timeout_s"]);
                  }}
                />
              </Field>
              <Field label={t("probes.form.failAfterLabel")} description={t("probes.form.failAfterDescription")} error={message("fail_after")}>
                <Input
                  mono
                  inputMode="numeric"
                  value={form.failAfter}
                  onValueChange={(next: string) => {
                    set("failAfter", next, ["fail_after"]);
                  }}
                />
              </Field>
            </div>
          </div>
        </Disclosure>
      </form>
    </Drawer>
  );
}
