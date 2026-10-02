/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ash: "#8e9b90", teal: "#93c0a4", sage: "#b6c4a2", pearl: "#d4cdab", beige: "#dce2bd",
        bg: "#12150e", surface: "#1b2016", surface2: "#242b1d", edge: "#3b4531",
        ink: "#ebecd7", muted: "#9fac92", bad: "#dd8f76",
      },
      borderRadius: { xl2: "14px" },
      fontFamily: { sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"] },
    },
  },
  plugins: [],
};
