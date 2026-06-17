import {create} from "zustand";

export type ColorScheme = "light" | "dark";

const STORAGE_KEY = "mhm-color-scheme";

function readStored(): ColorScheme {
  if (typeof window === "undefined") return "dark";
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === "light" ? "light" : "dark";
}

export function applyColorScheme(scheme: ColorScheme): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", scheme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute("content", scheme === "light" ? "#eef6f2" : "#00190d");
  }
}

/** Call once before React mounts to avoid a theme flash. */
export function initTheme(): ColorScheme {
  const scheme = readStored();
  applyColorScheme(scheme);
  return scheme;
}

interface ThemeState {
  colorScheme: ColorScheme;
  setColorScheme: (scheme: ColorScheme) => void;
}

export const useTheme = create<ThemeState>((set) => ({
  colorScheme: readStored(),
  setColorScheme(scheme) {
    localStorage.setItem(STORAGE_KEY, scheme);
    applyColorScheme(scheme);
    set({colorScheme: scheme});
  },
}));
