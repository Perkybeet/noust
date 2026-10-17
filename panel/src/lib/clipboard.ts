/**
 * Copies text to the clipboard.
 *
 * The async Clipboard API only exists in secure contexts, and a Noust panel is often reached
 * over plain HTTP on a private network before TLS is set up. The fallback selects a detached
 * textarea and uses the legacy copy command, which browsers still honour on a user gesture.
 *
 * @throws Error when neither mechanism is available or the browser refuses the copy.
 */
export async function copyText(text: string): Promise<void> {
  if (window.isSecureContext && "clipboard" in navigator) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.className = "fixed -left-[9999px] top-0 opacity-0";
  document.body.append(area);
  area.select();
  try {
    // execCommand is deprecated but is the only copy path outside a secure context.
    // eslint-disable-next-line @typescript-eslint/no-deprecated
    const ok = document.execCommand("copy");
    if (!ok) throw new Error("The browser refused to copy to the clipboard.");
  } finally {
    area.remove();
  }
}

/** Offers a blob as a file download without a server round trip. */
function offer(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  // Revoke on the next task so the navigation has started with the URL still valid.
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}

/** Offers text as a file download without a server round trip. */
export function downloadText(filename: string, text: string, type = "text/plain"): void {
  offer(filename, new Blob([text], { type: `${type};charset=utf-8` }));
}

/** Offers bytes (given as base64, the way the API carries a binary file) as a file download. */
export function downloadBase64(filename: string, base64: string, type = "application/octet-stream"): void {
  const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  offer(filename, new Blob([bytes], { type }));
}
