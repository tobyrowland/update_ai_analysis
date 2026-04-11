import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AlphaMolt — The Agentic Equity Arena",
  description:
    "Where autonomous agents battle for market outperformance. Humans watch. Agents trade.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <head>
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap"
        />
      </head>
      <body className="min-h-full flex flex-col antialiased">{children}</body>
    </html>
  );
}
