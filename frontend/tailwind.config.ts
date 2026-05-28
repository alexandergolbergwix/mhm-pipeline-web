import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Liquid-glass design tokens — Phase 8 wires the actual R3F
      // transmission surfaces; these are the fallback frosted-glass
      // values consumed by the CSS-only path.
      colors: {
        glass: {
          base: "rgba(255,255,255,0.08)",
          edge: "rgba(255,255,255,0.18)",
          ink: "#e9edf2",
          inkSub: "#a8b1bd",
        },
      },
      backdropBlur: {
        glass: "24px",
      },
      boxShadow: {
        glass:
          "0 8px 32px 0 rgba(0,0,0,0.36), inset 0 1px 0 rgba(255,255,255,0.18)",
      },
    },
  },
  plugins: [],
} satisfies Config;
