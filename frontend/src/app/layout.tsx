import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";

export const metadata: Metadata = {
  title: "AgentGuard",
  description: "Financial protection for autonomous AI transactions",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen md:grid md:grid-cols-[232px_1fr]">
        <Sidebar />
        <main className="min-w-0 px-5 py-8 md:px-10 md:py-10">
          <div className="mx-auto max-w-[1180px]">{children}</div>
        </main>
      </body>
    </html>
  );
}
