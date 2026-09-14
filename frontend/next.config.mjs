/** @type {import('next').NextConfig} */
const nextConfig = {
  // The web app is a pure client of the FastAPI backend; set NEXT_PUBLIC_API_URL
  // to point it somewhere other than http://localhost:8000.
  reactStrictMode: true,

  // Emit .next/standalone: a self-contained server bundled with only the modules it
  // actually imports. Without this the runtime image has to carry all of
  // node_modules, which for antd + MUI + recharts is most of the image.
  output: "standalone",
};

export default nextConfig;
