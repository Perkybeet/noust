import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import type { ProcessOwner } from "./processOwner";

/** The application or the service a process belongs to, in mono: what a person recognises of it. */
export function OwnerName({ owner }: { owner: ProcessOwner | null }) {
  if (owner === null) return <EmptyCell />;
  return <Mono tone="muted">{owner.kind === "app" ? owner.domain : owner.unit}</Mono>;
}
