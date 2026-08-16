import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "NotionDrive — Unlimited Cloud Storage",
  description: "OneDrive-grade cloud file manager powered by Notion Database",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} font-sans antialiased bg-[#0d0f11] text-white`}>
        {children}
      </body>
    </html>
  );
}
