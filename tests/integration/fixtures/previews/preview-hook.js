// A hook of a preview's life: says what it ran as and what it was told, in the job's log.
const phase = process.argv[2];
console.log(
  `PREVIEW_HOOK ${phase} of=${process.env.NOUST_PREVIEW_OF || "-"} ` +
    `pr=${process.env.NOUST_PR_NUMBER || "-"} cwd-has-server=${require("node:fs").existsSync("server.js")}`,
);
