import { defineConfig } from "vite";
import { readFileSync } from 'node:fs';
const version=JSON.parse(readFileSync(new URL('./package.json',import.meta.url),'utf8')).version;

export default defineConfig({
  clearScreen: false,
  define: {__ALDEN_VERSION__:JSON.stringify(version)},
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
  },
});
