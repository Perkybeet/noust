/**
 * What was drawn, frame by frame, while one page handed over to another. A hand-over is judged by its pictures, and a
 * test that only waits for the new address never sees the frames in between: the second column of the next page
 * beside the old page, or its loading shapes, each there for a few tens of milliseconds.
 */

import type { Page } from "@playwright/test";

/** One picture: the address, the page's title, and the loading regions on screen. */
export interface Frame {
  path: string;
  title: string;
  loading: string[];
}

interface Recording {
  frames: Frame[];
  last: string;
  stop: boolean;
}

declare global {
  interface Window {
    __noustFrames?: Recording;
  }
}

/** Starts writing down a frame every time what is drawn changes. */
export async function recordFrames(page: Page): Promise<void> {
  await page.evaluate(() => {
    const recording: Recording = { frames: [], last: "", stop: false };
    window.__noustFrames = recording;
    const snap = (): void => {
      if (recording.stop) return;
      const frame: Frame = {
        path: window.location.pathname,
        title: document.querySelector("h1")?.textContent ?? "",
        loading: [...document.querySelectorAll('[aria-busy="true"]')].map((region) => region.textContent.trim()).filter((text) => text.startsWith("Loading")),
      };
      const key = JSON.stringify(frame);
      if (key !== recording.last) {
        recording.last = key;
        recording.frames.push(frame);
      }
      requestAnimationFrame(snap);
    };
    requestAnimationFrame(snap);
  });
}

/** Stops recording and returns the frames, in the order they were drawn. */
export async function recordedFrames(page: Page): Promise<Frame[]> {
  return page.evaluate(() => {
    const recording = window.__noustFrames;
    if (recording === undefined) return [];
    recording.stop = true;
    return recording.frames;
  });
}
