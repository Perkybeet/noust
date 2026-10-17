import { useQuery } from "@tanstack/react-query";

import { complianceQuery } from "../../../api/queries/compliance";

/**
 * Whether a decision that lowers protection (building as root, accepting a risk, a privileged
 * container) has to say why on this server: only where a compliance framework asks for it or the
 * operator turned `security.exception_reasons` on. The server decides again when the form is sent;
 * this only keeps the form from demanding a reason nobody asked for. Until the answer is known the
 * reason is asked for, which is the stricter reading.
 */
export function useReasonRequired(): boolean {
  const { data } = useQuery({
    ...complianceQuery(),
    select: (state) => state.controls.find((control) => control.id === "exception_reasons")?.enabled ?? true,
  });
  return data ?? true;
}
