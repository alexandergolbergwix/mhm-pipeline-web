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
          DEFAULT: "#eaf6fb",
          muted:   "#b7d8e3",
        },
        glass: {
          base:     "rgba(255,255,255,0.08)",
          strong:   "rgba(255,255,255,0.12)",
          line:     "rgba(119,204,229,0.24)",
          inkSub:   "#b7d8e3",
          ink:      "#eaf6fb",
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
