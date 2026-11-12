/**
 * Changing where a rule of the firewall admits from, in one step. Before, it was two: delete the rule and make the one
 * that says the new origin, with the confirmation of an SSH login between them. Here it is one change that waits once,
 * and that puts the old rule back when nobody confirms it, like every change that could cut a person off.
 *
 * The server judges the origin again and holds the guard: moving the rule that lets everyone reach SSH or the console is
 * refused when a session open now would be left outside the new origin, and says which. This dialog says that before it
 * is asked, for the rules the guard keeps.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useServerConfirm } from "../server/errors";
import type { FirewallRule } from "../server/queries";
import { serverKeys } from "../server/queries";
import { useServerJob } from "../server/serverJob";
import { rulePorts, sourceBody, sourceProblem } from "./firewallRules";
import type { SourceChoice } from "./firewallRules";

export interface ChangeSourceDialogProps {
  rule: FirewallRule;
  /** Noust keeps this rule's port open (SSH, or a console on a public name). */
  guarded: boolean;
  onClose: () => void;
}

export function ChangeSourceDialog({ rule, guarded, onClose }: ChangeSourceDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const confirmation = useServerConfirm();
  const jobs = useServerJob();
  const everyone = rule.source === "any" || rule.source === "";
  const [who, setWho] = useState<SourceChoice>("some");
  const [source, setSource] = useState(everyone ? "" : rule.source);
  const [touched, setTouched] = useState(false);
  const choice: SourceChoice = everyone ? "some" : who;
  const problem = sourceProblem(choice, source, rule.source);
  const text =
    problem === null
      ? null
      : problem === "needed"
        ? t("network.firewall.sourceNeeded")
        : problem === "same"
          ? t("network.firewall.sourceSame")
          : problem === "one"
            ? t("network.firewall.sourceOne")
            : t("network.firewall.sourceInvalid");

  return (
    <ConfirmDialog
      {...confirmation}
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      friction="simple"
      destructive={false}
      size="md"
      title={t("network.firewall.sourceTitle", { rule: `${rulePorts(rule)}/${rule.proto}` })}
      description={t("network.firewall.sourceDescription")}
      actionLabel={t("network.firewall.sourceAction")}
      ready={problem === null}
      onConfirm={async () => {
        const accepted = await request("post", "/api/server/security/firewall/rules/{rule_id}/source", {
          params: { rule_id: rule.id },
          body: { source: sourceBody(choice, source) },
        });
        jobs.track(accepted.job_id, "security");
        void queryClient.invalidateQueries({ queryKey: serverKeys.firewall });
      }}
    >
      <div className="flex min-w-0 flex-col gap-5">
        <p className="text-13 text-fg-muted">
          {t("network.firewall.sourceNow")}{" "}
          {everyone ? <span className="text-fg">{t("server.firewall.rules.anywhere")}</span> : <Mono>{rule.source}</Mono>}
        </p>
        {everyone ? null : (
          <SegmentedControl<SourceChoice>
            label={t("network.firewall.sourceChoice")}
            value={who}
            onValueChange={setWho}
            options={[
              { value: "some", label: t("network.firewall.sourceOnlyThese") },
              { value: "any", label: t("network.firewall.sourceAnyone") },
            ]}
            className="self-start"
          />
        )}
        {choice === "some" ? (
          <Field label={t("network.firewall.sourceLabel")} description={t("network.firewall.sourceHint")} error={touched ? text : null}>
            <Input
              mono
              value={source}
              onValueChange={(value: string) => {
                setSource(value);
                setTouched(true);
              }}
              autoComplete="off"
              spellCheck={false}
              placeholder="10.0.0.0/24"
            />
          </Field>
        ) : null}
        {guarded ? (
          <Notice tone="warning" title={t("network.firewall.sourceGuardTitle")}>
            <p>{t("network.firewall.sourceGuard")}</p>
          </Notice>
        ) : null}
        <p className="text-13 text-fg-muted">{t("server.firewall.reverts")}</p>
      </div>
    </ConfirmDialog>
  );
}
