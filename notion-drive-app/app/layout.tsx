import type { Metadata } from "next";
import { Inter } from "next/font/google";
import ThemeProvider from "@/components/ThemeProvider";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "NotionDrive — Unlimited Cloud Storage",
  description: "Google Drive-style file manager powered by your Notion database",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} font-sans antialiased bg-[var(--bg)] text-[var(--text)]`}>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
