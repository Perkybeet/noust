/**
 * A server's compliance answer for tests: what `GET /api/compliance` says when nothing is on (the
 * default), and helpers to build the answers a change would give. Small on purpose: three
 * frameworks and a handful of controls are enough to show every state the console draws.
 */

export interface FakeFramework {
  id: string;
  name: string;
  family: string;
  level: string;
  summary: string;
  reference: string;
  enabled: boolean;
  source: string | null;
  turns_on: string[];
  requirement_count: number;
  changes: { control: string; before: unknown; after: unknown; clauses: string[] }[];
  organisational: string[];
  not_covered: string[];
  includes: string | null;
}

export interface FakeControl {
  id: string;
  group: string;
  kind: string;
  standard: unknown;
  key: string;
  choices: string[];
  unit: string;
  check_only: boolean;
  value: unknown;
  operator: unknown;
  floor: unknown;
  forced_by: string[];
  locked: boolean;
  enabled: boolean;
}

export function control(id: string, extra: Partial<FakeControl> = {}): FakeControl {
  return {
    id,
    group: "access",
    kind: "flag",
    standard: false,
    key: `security.${id}`,
    choices: [],
    unit: "",
    check_only: false,
    value: false,
    operator: false,
    floor: false,
    forced_by: [],
    locked: false,
    enabled: false,
    ...extra,
  };
}

export const CONTROLS: FakeControl[] = [
  control("approvals", { key: "approval.enabled" }),
  control("separation_of_duties"),
  control("session_idle_minutes", {
    kind: "min",
    standard: 30,
    value: 30,
    operator: 30,
    floor: null,
    unit: "minutes",
    key: "auth.session.idle_minutes",
  }),
  control("change_reasons", { group: "accountability" }),
  control("backup_encryption", {
    group: "backup",
    kind: "choice",
    standard: "optional",
    value: "optional",
    operator: "optional",
    floor: "optional",
    choices: ["optional", "required"],
    key: "backup.encryption",
  }),
  control("console_exposure", {
    group: "network",
    kind: "choice",
    standard: "allowed",
    value: "allowed",
    operator: "allowed",
    floor: "allowed",
    choices: ["allowed", "refused"],
  }),
];

export const FRAMEWORKS: FakeFramework[] = [
  {
    id: "ens-media",
    name: "ENS MEDIA",
    family: "ens",
    level: "MEDIA",
    summary: "Spain's National Security Scheme, category MEDIA.",
    reference: "Real Decreto 311/2022, Annex II",
    enabled: false,
    source: null,
    turns_on: ["approvals", "session_idle_minutes"],
    requirement_count: 38,
    changes: [
      { control: "approvals", before: false, after: true, clauses: ["op.acc.3.2", "op.exp.5.4"] },
      { control: "session_idle_minutes", before: 30, after: 15, clauses: ["mp.eq.2.1"] },
    ],
    organisational: ["The risk analysis and the categorisation of the system.", "People: duties, awareness and training."],
    not_covered: [],
    includes: null,
  },
  {
    id: "iso-27001",
    name: "ISO/IEC 27001:2022",
    family: "iso",
    level: "",
    summary: "The international information security management standard.",
    reference: "ISO/IEC 27001:2022, Annex A",
    enabled: false,
    source: null,
    turns_on: ["separation_of_duties", "session_idle_minutes"],
    requirement_count: 37,
    changes: [
      { control: "separation_of_duties", before: false, after: true, clauses: ["A.5.3"] },
      { control: "session_idle_minutes", before: 30, after: 15, clauses: ["A.8.5"] },
    ],
    organisational: ["The management system itself.", "Certification, which only an accredited body grants."],
    not_covered: [],
    includes: null,
  },
  {
    id: "nis2",
    name: "NIS2",
    family: "nis2",
    level: "",
    summary: "The EU directive on the security of network and information systems.",
    reference: "Directive (EU) 2022/2555, article 21",
    enabled: false,
    source: null,
    turns_on: ["backup_encryption"],
    requirement_count: 20,
    changes: [{ control: "backup_encryption", before: "optional", after: "required", clauses: ["Art. 21(2)(h)"] }],
    organisational: ["Governance: management approves the measures.", "Reporting a significant incident."],
    not_covered: [],
    includes: null,
  },
];

export const COMPLIANCE = {
  frameworks: FRAMEWORKS,
  controls: CONTROLS,
  legacy_profile: "standard",
  unknown: [] as string[],
};

/** The answer with frameworks switched on, and the controls they force locked at their values. */
export function withFrameworksOn(...ids: string[]) {
  const frameworks = FRAMEWORKS.map((framework) => ({
    ...framework,
    enabled: ids.includes(framework.id),
    source: ids.includes(framework.id) ? "setting" : null,
    changes: ids.includes(framework.id) ? [] : framework.changes,
  }));
  const controls = CONTROLS.map((entry) => {
    const forcing = frameworks.filter((framework) => framework.enabled && framework.turns_on.includes(entry.id));
    const first = forcing[0];
    if (first === undefined) return entry;
    // What the first framework asks of it, from its own list of changes before it was on.
    const asked = FRAMEWORKS.find((framework) => framework.id === first.id)?.changes.find((change) => change.control === entry.id)?.after;
    return { ...entry, value: asked ?? entry.value, floor: asked ?? entry.floor, forced_by: forcing.map((framework) => framework.id), locked: true, enabled: true };
  });
  return { ...COMPLIANCE, frameworks, controls };
}

/** What a plan answers for turning one framework on. */
export function planFor(id: string, warnings: { id: string; count?: number | null }[] = []) {
  const framework = FRAMEWORKS.find((entry) => entry.id === id);
  return {
    enable: [id],
    disable: [],
    settings: {},
    enabled_after: [id],
    effects: (framework?.changes ?? []).map((change) => ({
      control: change.control,
      before: change.before,
      after: change.after,
      forced_by: [id],
      clauses: { [id]: change.clauses },
    })),
    loosens: false,
    warnings,
  };
}
