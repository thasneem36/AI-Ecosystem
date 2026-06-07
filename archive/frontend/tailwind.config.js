/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "#0f0f0f", // page background
        sidebar: "#1a1a1a", // sidebar background
        card: "#1e1e1e", // cards / panels
        accent: "#00ff88", // primary accent (green)
        agent: {
          planner: "#facc15", // yellow
          executor: "#22d3ee", // cyan
          coding: "#22c55e", // green
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        blink: {
          "0%, 100%": { opacity: "0.2" },
          "50%": { opacity: "1" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.3s ease-out",
        blink: "blink 1.2s infinite",
      },
    },
  },
  plugins: [],
};
