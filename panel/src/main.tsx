import "@fontsource-variable/mona-sans/wdth.css";
import "@fontsource-variable/jetbrains-mono/wght.css";
import "./styles/app.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App, createQueryClient } from "./app/App";
import { getLocale, initLocale } from "./app/locale";
import { createAppRouter, namespacesOfLocation } from "./app/router";
import { initTheme } from "./app/theme";
import { installSessionHandling } from "./features/auth/session";
import { startIdlePreload } from "./i18n/preload";

const container = document.getElementById("root");
if (!container) {
  throw new Error("index.html must contain <div id=\"root\"> for the console to mount.");
}

// Before the first render, so a pinned theme never flashes the other one.
initTheme();

const queryClient = createQueryClient();
const router = await createAppRouter(queryClient);
// Also before it: the chrome of the operator's language, and the texts of the page the address
// opens, are fetched together, so the first paint is already in that language, with no English
// flash and no reflow. English has its chrome in the bundle; every other namespace is a chunk.
await initLocale(namespacesOfLocation(router));
installSessionHandling(router, queryClient);

if (import.meta.env.DEV) {
  const { exposeDevTools } = await import("./dev/devtools");
  exposeDevTools();
}

createRoot(container).render(
  <StrictMode>
    <App router={router} queryClient={queryClient} />
  </StrictMode>,
);
// The rest of the language, a few chunks at a time while the browser is idle, so that the next
// page an operator opens has its texts already.
startIdlePreload(getLocale);
