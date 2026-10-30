export const DIGEST = `sha256:${"a".repeat(64)}`;

export const INSTANCE = {
  name: "pg16",
  key: "postgresql@pg16",
  engine: "postgresql",
  flavour: "postgresql",
  display_name: "PostgreSQL",
  version: "16",
  image: "postgres",
  digest: DIGEST,
  previous_digest: null,
  reference: `postgres:16@${DIGEST}`,
  host_port: 5433,
  address: "127.0.0.1",
  memory_limit_mb: 512,
  state: "ready",
  running: true,
  status: "running",
  unit: "noust-db-pg16",
  container: "noust-db-pg16",
  volume: "noust-db-pg16-data",
  directory: "/var/lib/noust/db-instances/pg16",
  created_at: "2026-10-01T10:00:00+00:00",
  created_by: null,
  updated_at: "2026-10-01T10:00:00+00:00",
  upgraded_at: null,
  end_of_life: null,
  support: null,
  tls_mode: "off",
};

export const BUDGET = { server_mb: 8192, allocatable_mb: 7372, allocated_mb: 512, available_mb: 6860 };

export const CATALOG = {
  engines: [
    {
      flavour: "postgresql",
      engine: "postgresql",
      display_name: "PostgreSQL",
      image: "postgres",
      container_port: 5432,
      min_memory_mb: 256,
      default_memory_mb: 512,
      // 12 lost its support in 2024 and needs the person to say they know; 14 is about to end (its date is far off so the test never ages).
      versions: [
        { version: "12", digest: DIGEST, default: false, end_of_life: "2024-11-21", support: "ended", unsupported: true, platforms: ["amd64", "arm64"] },
        { version: "14", digest: DIGEST, default: false, end_of_life: "2099-12-31", support: "ending_soon", unsupported: true, platforms: ["amd64", "arm64"] },
        { version: "15", digest: DIGEST, default: false, end_of_life: "2027-11-11", support: "supported", unsupported: false, platforms: ["amd64", "arm64"] },
        { version: "16", digest: DIGEST, default: false, end_of_life: "2028-11-09", support: "supported", unsupported: false, platforms: ["amd64", "arm64"] },
        { version: "17", digest: DIGEST, default: false, end_of_life: "2029-11-08", support: "supported", unsupported: false, platforms: ["amd64", "arm64"] },
        { version: "18", digest: DIGEST, default: true, end_of_life: "2030-11-14", support: "supported", unsupported: false, platforms: ["amd64", "arm64"] },
      ],
      variants: [
        {
          name: "pgvector",
          display_name: "PostgreSQL with pgvector",
          description: "Vector similarity search: the vector type and its indexes.",
          image: "pgvector/pgvector",
          platforms: ["amd64", "arm64"],
          versions: ["16", "17", "18"].map((version) => ({ version, tag: `pg${version}`, digest: DIGEST, default: version === "18", end_of_life: null })),
        },
        {
          name: "postgis",
          display_name: "PostgreSQL with PostGIS",
          description: "Geographic objects.",
          image: "postgis/postgis",
          platforms: ["amd64"],
          versions: ["17", "18"].map((version) => ({ version, tag: `${version}-3.5`, digest: DIGEST, default: version === "18", end_of_life: null })),
        },
      ],
    },
    {
      flavour: "redis",
      engine: "redis",
      display_name: "Redis",
      image: "redis",
      container_port: 6379,
      min_memory_mb: 64,
      default_memory_mb: 256,
      versions: [{ version: "8.0", digest: DIGEST, default: true, end_of_life: null }],
    },
  ],
};

/** The instance as the list of database servers says it: a container of Noust's, which can be made the default, updated and removed. */
export const INSTANCE_SERVER = {
  key: "postgresql@pg16",
  engine: "postgresql",
  flavour: "postgresql",
  display_name: "PostgreSQL",
  version: "16",
  runs_in: "noust_container",
  name: "pg16",
  state: "running",
  running: true,
  port: 5433,
  memory_limit_mb: 512,
  image: "postgres",
  digest: DIGEST,
  custom_image: false,
  support: "supported",
  end_of_life: "2028-11-09",
  databases: 0,
  default: false,
  default_source: null,
  actions: ["stop", "set_default", "other_version", "upgrade", "remove"],
};

export const JOB = { id: "j1", type: "database", name: "Job", description: "", status: "running", progress: 10, total_steps: 100, current_step: "Working", created_at: "2026-10-06T10:00:00", logs: [], metadata: {} };
export const ACCEPTED = { job_id: "j1", status: "pending", message: "queued", job: JOB };
