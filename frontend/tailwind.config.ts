import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: "#0F172A",
        "surface-raised": "#1E293B",
        accent: "#22C55E",
        danger: "#EF4444",
      },
      fontFamily: {
        sans: ["Fira Sans", "system-ui", "sans-serif"],
        mono: ["Fira Code", "monospace"],
      },
      borderRadius: {
        xl: "0.75rem",
      },
      keyframes: {
        "price-flash": {
          "0%": { backgroundColor: "rgba(34, 197, 94, 0.35)" },
          "100%": { backgroundColor: "transparent" },
        },
      },
      animation: {
        "price-flash": "price-flash 1.2s ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
