import type { Config } from "tailwindcss";

/* Bar-Ilan palette — lifted from the presentation deck so the web app
   carries the same identity. Source: pipeline/docs/presentations/
   bar-ilan-phd-pipeline-presentation.html (:root custom properties). */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        biu: {
          navy:  "#00190d",
          black: "#000000",
          green: "#004027",
          sky:   "#77cce5",
        },
        ink: {
          DEFAULT: "var(--ink)",
          muted:   "var(--muted)",
        },
        glass: {
          base:     "var(--panel)",
          strong:   "var(--panel-strong)",
          line:     "var(--line)",
          inkSub:   "var(--muted)",
          ink:      "var(--ink)",
        },
      },
      backdropBlur: {
        glass: "12px",
      },
      boxShadow: {
        glass: "0 24px 60px rgba(0,0,0,0.35)",
      },
      borderRadius: {
        glass: "26px",
      },
      fontFamily: {
        sans: [
          '"Avenir Next"', '"Segoe UI"', '"Helvetica Neue"',
          "ui-sans-serif", "system-ui", "sans-serif",
        ],
      },
    },
  },
  plugins: [],
} satisfies Config;
