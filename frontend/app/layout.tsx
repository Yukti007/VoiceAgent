import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

// Apple devices render SF Pro via the system stack in globals.css; everyone
// else falls through to Inter, the closest freely-licensed match.
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "Sharma Dental Care — Aisha Voice Receptionist",
  description: "Demo: multilingual AI voice receptionist (V0)",
};

export const viewport: Viewport = {
  themeColor: "#fbf8ef",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
