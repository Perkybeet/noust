import type { SelectOption } from "../../../components/ui/Select";
import type { T } from "../../../i18n";

/** The kinds that have no address of their own: the operator names the host. */
const NEEDS_SERVER: ReadonlySet<string> = new Set(["ecr", "acr", "gar", "custom"]);

const KIND_WORDS = {
  dockerhub: "registries.kind.dockerhub",
  ghcr: "registries.kind.ghcr",
  gitlab: "registries.kind.gitlab",
  ecr: "registries.kind.ecr",
  acr: "registries.kind.acr",
  gar: "registries.kind.gar",
  custom: "registries.kind.custom",
} as const;

type Kind = keyof typeof KIND_WORDS;

function isKind(value: string): value is Kind {
  return value in KIND_WORDS;
}

/** Whether the host of this kind of registry must be given (the others have a host of their own). */
export function needsServer(kind: string): boolean {
  return NEEDS_SERVER.has(kind);
}

/** A kind, in words. A kind a newer server knows and this console does not is shown as it is. */
export function kindWords(t: T, kind: string): string {
  return isKind(kind) ? t(KIND_WORDS[kind]) : kind;
}

/** The kinds the server lists, as a choice. */
export function kindOptions(t: T, kinds: readonly string[]): SelectOption[] {
  return kinds.map((kind) => ({ value: kind, label: kindWords(t, kind) }));
}

/** ECR's "username" is an AWS access key id; the other kinds' is the account's name. */
export function usernameLabelKey(kind: string): "registries.dialog.usernameEcr" | "registries.dialog.username" {
  return kind === "ecr" ? "registries.dialog.usernameEcr" : "registries.dialog.username";
}

/** ECR's "password" is the AWS secret access key. */
export function passwordLabelKey(kind: string): "registries.dialog.passwordEcr" | "registries.dialog.password" {
  return kind === "ecr" ? "registries.dialog.passwordEcr" : "registries.dialog.password";
}
