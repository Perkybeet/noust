-- An additive migration the harness commits to its clone of Proggest: a new table that
-- nothing reads, so the code before it runs unchanged against the schema after it.
CREATE TABLE "noust_it_probe" (
    "id" SERIAL NOT NULL,
    "note" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "noust_it_probe_pkey" PRIMARY KEY ("id")
);
