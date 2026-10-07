/** What `GET /api/databases/provisioning/choices` answers on a server with PostgreSQL and Redis installed. */

import type { EngineChoice, ProvisioningPlan } from "../../../api/queries/provisioning";

export const CHOICES: EngineChoice[] = [
  {
    flavour: "postgresql",
    engine: "postgresql",
    display_name: "PostgreSQL",
    installed: true,
    running_version: "16.4",
    installable: false,
    reason: "PostgreSQL is installed.",
    versions: [
      { version: "15", state: "unavailable", default: false },
      { version: "16", state: "installed", default: true },
      { version: "17", state: "unavailable", default: false },
    ],
  },
  {
    flavour: "mariadb",
    engine: "mysql",
    display_name: "MariaDB",
    installed: false,
    running_version: null,
    installable: false,
    reason: "MySQL is installed, and MariaDB cannot run beside it.",
    versions: [{ version: "11.4", state: "unavailable", default: true }],
  },
  {
    flavour: "mongodb",
    engine: "mongodb",
    display_name: "MongoDB",
    installed: false,
    running_version: null,
    installable: true,
    reason: null,
    versions: [
      { version: "7.0", state: "installable", default: false },
      { version: "8.0", state: "installable", default: true },
    ],
  },
  {
    flavour: "redis",
    engine: "redis",
    display_name: "Redis",
    installed: true,
    running_version: null,
    installable: false,
    reason: null,
    versions: [{ version: "7.0", state: "unavailable", default: true }],
  },
];

export function planFor(engine: string, extra: Partial<ProvisioningPlan> = {}): ProvisioningPlan {
  return {
    engine,
    display_name: engine === "mongodb" ? "MongoDB" : "PostgreSQL",
    installed: engine !== "mongodb",
    running: engine !== "mongodb",
    database: "shop_example_com",
    username: "shop_example_com",
    env_vars: ["DATABASE_URL"],
    url: "postgresql://shop_example_com:********@127.0.0.1:5432/shop_example_com",
    database_exists: false,
    version: null,
    running_version: engine === "mongodb" ? null : "16.4",
    will_install: engine === "mongodb",
    ...extra,
  };
}
