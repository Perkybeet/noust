import { ToggleLeft, ToggleRight } from "lucide-react";

import { Badge } from "../../../components/ui/Badge";

/**
 * Whether a setting is on: a word and a switch's drawing, neutral. A setting that is on is not
 * a running state, so it is never green (docs/DESIGN.md, "Colour").
 */
export function SettingState({ on, label }: { on: boolean; label: string }) {
  const Icon = on ? ToggleRight : ToggleLeft;
  return (
    <Badge>
      <Icon aria-hidden="true" className="size-icon-sm" />
      {label}
    </Badge>
  );
}
