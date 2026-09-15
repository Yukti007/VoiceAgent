import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sharma Dental Care — Aisha Voice Receptionist",
  description: "Demo: multilingual AI voice receptionist (V0)",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
