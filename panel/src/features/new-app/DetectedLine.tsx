import { Button } from "../../components/ui/Button";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { useT } from "../../i18n";
import { hasPort, typeName } from "./wizard";
import type { AppTypeOption, Inspection } from "./wizard";

export interface DetectedLineProps {
  inspection: Inspection;
  types: readonly AppTypeOption[];
  /** Opens the step where what was found is changed. */
  onChange: () => void;
}

/**
 * What was found in a source, in a line, as soon as it is read: "Detected: Next.js with npm. Port
 * 3000. 2 variables to fill in." A person who pasted an address sees what Noust understood of it
 * before pressing Continue, and "Change" opens the step that edits it. Whole sentences, joined:
 * the line is read, not assembled, so each can be translated on its own.
 */
export function DetectedLine({ inspection, types, onChange }: DetectedLineProps) {
  const t = useT();
  if (inspection.detected_types.length === 0) return <InlineMessage tone="info">{t("newApp.source.detectedNothing")}</InlineMessage>;
  const type = typeName(types, inspection.app_type);
  const toFill = inspection.env_keys.filter((key) => key.required && (key.default === null || key.default === undefined || key.default === "")).length;
  const sentences = [
    inspection.package_manager ? t("newApp.source.detectedUsing", { type, manager: inspection.package_manager }) : t("newApp.source.detected", { type }),
    hasPort(inspection.app_type) ? t("newApp.source.detectedPort", { port: String(inspection.default_port) }) : null,
    toFill > 0 ? t("newApp.source.detectedVariables", { count: toFill }) : null,
  ].filter((sentence): sentence is string => sentence !== null);
  return (
    <div role="status" className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <p className="text-13 text-pretty text-fg">{sentences.join(" ")}</p>
      <Button size="sm" variant="ghost" onClick={onChange}>
        {t("newApp.source.change")}
      </Button>
    </div>
  );
}
