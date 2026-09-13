import type { Metadata } from "next";
import { Montserrat, Poppins } from "next/font/google";
import { AntdProvider } from "@/components/AntdProvider";
import "./globals.css";
import "./app-shell.css";

// Self-hosted by next/font: no render-blocking request to Google and no layout
// shift from a late swap. Poppins carries the body, Montserrat the headings —
// both geometric sans, so they sit together without clashing.
const poppins = Poppins({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-poppins",
});

const montserrat = Montserrat({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-montserrat",
});

export const metadata: Metadata = {
  title: "RecruitPro",
  description:
    "Upload resumes once into a reusable candidate repository, then match any job description against it — with transparent scores and evidence for every rank.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${poppins.variable} ${montserrat.variable}`}>
      <body>
        <AntdProvider>{children}</AntdProvider>
      </body>
    </html>
  );
}
