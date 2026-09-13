/** @type {import('next').NextConfig} */
const nextConfig = {
  // The web app is a pure client of the FastAPI backend; set NEXT_PUBLIC_API_URL
  // to point it somewhere other than http://localhost:8000.
  reactStrictMode: true,
};

export default nextConfig;
