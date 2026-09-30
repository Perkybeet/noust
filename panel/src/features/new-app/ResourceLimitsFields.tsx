import { Subsection } from "../../components/page/Subsection";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import type { LimitsDraft } from "../app/settings/limits";
import { limitField } from "./wizard";
import type { ReviewErrors } from "./wizard";

export interface ResourceLimitsFieldsProps {
  draft: LimitsDraft;
  /** CPUs of this machine, when known, for the CPU quota's description. */
  cores: number | null;
  errors: ReviewErrors;
  onChange: (draft: LimitsDraft) => void;
}

/**
 * The most the app may use of the server: memory, CPU, and processes and threads. Empty is no
 * limit, exactly as the app's own Settings tab treats one (`PATCH /api/apps/{domain}/limits`),
 * with the same bounds and the same words, so the two never disagree. systemd's own names for
 * them (MemoryMax, CPUQuota, TasksMax) are left to the command line.
 */
export function ResourceLimitsFields({ draft, cores, errors, onChange }: ResourceLimitsFieldsProps) {
  const t = useT();
  const set = (patch: Partial<LimitsDraft>): void => {
    onChange({ ...draft, ...patch });
  };

  return (
    <Subsection title={t("newApp.limits.title")} description={t("newApp.limits.description")}>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label={t("newApp.limits.memory")} optional description={t("newApp.limits.memoryDescription")} error={errors[limitField("memory")]}>
          <Input
            mono
            inputMode="numeric"
            autoComplete="off"
            placeholder={t("newApp.limits.none")}
            suffix="MB"
            value={draft.memory}
            onValueChange={(value: string) => set({ memory: value })}
          />
        </Field>
        <Field
          label={t("newApp.limits.cpu")}
          optional
          description={cores === null ? t("newApp.limits.cpuDescription") : t("newApp.limits.cpuDescriptionCores", { max: String(100 * cores) })}
          error={errors[limitField("cpu")]}
        >
          <Input
            mono
            inputMode="numeric"
            autoComplete="off"
            placeholder={t("newApp.limits.none")}
            suffix="%"
            value={draft.cpu}
            onValueChange={(value: string) => set({ cpu: value })}
          />
        </Field>
        <Field label={t("newApp.limits.tasks")} optional description={t("newApp.limits.tasksDescription")} error={errors[limitField("tasks")]}>
          <Input mono inputMode="numeric" autoComplete="off" placeholder={t("newApp.limits.none")} value={draft.tasks} onValueChange={(value: string) => set({ tasks: value })} />
        </Field>
      </div>
    </Subsection>
  );
}
