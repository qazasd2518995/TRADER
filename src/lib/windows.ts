import type { CaptureWindow, DetectedWindow } from "./types";

export function getCaptureWindowLabel(window: CaptureWindow): string {
  return window.display_name?.trim() || window.window_name;
}

export function captureWindowMatchesDetected(existing: CaptureWindow, detected: DetectedWindow): boolean {
  if (existing.window_id != null && detected.window_id != null) {
    return existing.window_id === detected.window_id;
  }
  return existing.window_name === detected.window_name && existing.app_name === (detected.owner || existing.app_name);
}

export function buildCaptureWindowFromDetected(detected: DetectedWindow, index: number): CaptureWindow {
  return {
    window_name: detected.window_name,
    app_name: detected.owner || "LINE",
    name: `win_${index}`,
    window_id: detected.window_id ?? undefined,
    display_name: detected.label || detected.window_name,
  };
}

export function buildManualCaptureWindow(title: string, index: number): CaptureWindow {
  return {
    window_name: title,
    app_name: "LINE",
    name: `win_${index}`,
    display_name: title,
  };
}

export function getDetectedWindowKey(window: DetectedWindow): string {
  if (window.window_id != null) return `id:${window.window_id}`;
  return `${window.owner}::${window.window_name}::${window.label}`;
}
