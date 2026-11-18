/**
 * `test` on the machine `console_server.py --docker` models: the default machine of the suite has no Docker on purpose
 * (so that the Software page can offer to install it), and what a container application or a volume's backup needs
 * of the machine is only said and checked on one that has it. A spec that needs both machines uses this beside `test`;
 * Playwright starts one server for each, in the worker that runs its tests.
 */

import { startConsoleServer, test as base } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

export const testWithDocker = base.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "600", "--docker"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 120_000 },
  ],
});
