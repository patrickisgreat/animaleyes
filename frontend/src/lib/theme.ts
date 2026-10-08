// Custom themes: a named set of colour tokens applied at runtime as CSS variables. The two
// built-ins (sage, aurora) live in index.css and are selected via [data-theme]; custom themes
// set the --c-* variables inline on :root (and cache them so there's no flash on reload).

export type ThemeDef = { id: string; name: string; colors: Record<string, string> };

export const TOKENS: { key: string; label: string }[] = [
  { key: "bg", label: "Background" },
  { key: "surface", label: "Panel" },
  { key: "surface2", label: "Panel 2" },
  { key: "edge", label: "Borders" },
  { key: "ink", label: "Text" },
  { key: "muted", label: "Muted text" },
  { key: "teal", label: "Primary" },
  { key: "accentInk", label: "Text on primary" },
  { key: "sage", label: "Accent 2" },
  { key: "pearl", label: "Accent 3" },
  { key: "beige", label: "Highlight" },
  { key: "ash", label: "Accent 4" },
  { key: "bad", label: "Warning" },
  { key: "title1", label: "Logo gradient A" },
  { key: "title2", label: "Logo gradient B" },
];

const CSSVAR: Record<string, string> = {
  bg: "--c-bg", surface: "--c-surface", surface2: "--c-surface2", edge: "--c-edge",
  ink: "--c-ink", muted: "--c-muted", bad: "--c-bad", teal: "--c-teal", sage: "--c-sage",
  pearl: "--c-pearl", beige: "--c-beige", ash: "--c-ash", accentInk: "--c-accent-ink",
  title1: "--c-title1", title2: "--c-title2",
};

export const BUILTINS = [
  { id: "sage", name: "Sage", swatch: "linear-gradient(135deg,#93c0a4,#dce2bd)" },
  { id: "aurora", name: "Aurora", swatch: "linear-gradient(135deg,#7400b8,#80ffdb)" },
];

export function hexToTriple(hex: string): string {
  const h = hex.replace("#", "");
  return `${parseInt(h.slice(0, 2), 16)} ${parseInt(h.slice(2, 4), 16)} ${parseInt(h.slice(4, 6), 16)}`;
}

export function tripleToHex(t: string): string {
  const [r, g, b] = t.trim().split(/\s+/).map(Number);
  if ([r, g, b].some((n) => Number.isNaN(n))) return "#000000";
  return "#" + [r, g, b].map((n) => Math.max(0, Math.min(255, n)).toString(16).padStart(2, "0")).join("");
}

function clearInline() {
  const root = document.documentElement;
  Object.values(CSSVAR).forEach((v) => root.style.removeProperty(v));
}

/** Apply a built-in theme by id, or a custom theme (its colours set inline + cached). */
export function applyTheme(id: string, custom?: ThemeDef) {
  const root = document.documentElement;
  clearInline();
  if (custom) {
    root.dataset.theme = "sage"; // base so any token the custom theme omits still resolves
    const vars: Record<string, string> = {};
    for (const [k, v] of Object.entries(custom.colors)) {
      const cv = CSSVAR[k];
      if (cv && /^#[0-9a-fA-F]{6}$/.test(v)) {
        const trip = hexToTriple(v);
        root.style.setProperty(cv, trip);
        vars[cv] = trip;
      }
    }
    try { localStorage.setItem("theme", id); localStorage.setItem("themeVars", JSON.stringify(vars)); } catch { /* ignore */ }
  } else {
    root.dataset.theme = id;
    try { localStorage.setItem("theme", id); localStorage.removeItem("themeVars"); } catch { /* ignore */ }
  }
}

/** Set colours inline for a live preview WITHOUT persisting (used while editing). */
export function previewColors(colors: Record<string, string>) {
  const root = document.documentElement;
  for (const [k, v] of Object.entries(colors)) {
    const cv = CSSVAR[k];
    if (cv && /^#[0-9a-fA-F]{6}$/.test(v)) root.style.setProperty(cv, hexToTriple(v));
  }
}

export const activeThemeId = () => {
  try { return localStorage.getItem("theme") || "sage"; } catch { return "sage"; }
};

/** The colours of the theme currently on screen, as hex — seeds the editor from "what I see". */
export function currentColors(): Record<string, string> {
  const cs = getComputedStyle(document.documentElement);
  const out: Record<string, string> = {};
  for (const t of TOKENS) {
    const trip = cs.getPropertyValue(CSSVAR[t.key]).trim();
    if (trip) out[t.key] = tripleToHex(trip);
  }
  return out;
}
