import { Button } from "../../../components/ui/Button";
import { CopyButton } from "../../../components/ui/CopyButton";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";

export interface SecretShownProps {
  /** What is shown: the account and its password, or an application's connection string. */
  secret: { title: string; description: string; fields: readonly { label: string; value: string }[] } | null;
  onClose: () => void;
}

/**
 * A secret, shown once (docs/DESIGN.md, "Forms"): in mono, each value with its copy button, and
 * "Done". Nothing here keeps it; closing forgets it.
 */
export function SecretShown({ secret, onClose }: SecretShownProps) {
  const t = useT();
  return (
    <Dialog
      open={secret !== null}
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      title={secret?.title ?? ""}
      description={secret?.description}
      footer={
        <Button variant="primary" onClick={onClose}>
          {t("databases.secret.done")}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        {(secret?.fields ?? []).map((field) => (
          <Field key={field.label} label={field.label}>
            <Input mono readOnly value={field.value} suffix={<CopyButton value={field.value} label={field.label} size="sm" />} />
          </Field>
        ))}
      </div>
    </Dialog>
  );
}
