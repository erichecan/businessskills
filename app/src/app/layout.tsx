import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "内容生产系统",
  description: "Eric 内容生产流水线",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-zinc-950 text-zinc-100">
        <nav className="border-b border-zinc-800 px-6 py-3 flex items-center gap-6 text-sm">
          <span className="font-semibold text-white tracking-tight">内容流水线</span>
          <Link href="/" className="text-zinc-400 hover:text-white transition-colors">仪表盘</Link>
          <Link href="/trends" className="text-zinc-400 hover:text-white transition-colors">热点</Link>
          <Link href="/topics" className="text-zinc-400 hover:text-white transition-colors">选题</Link>
          <Link href="/publish" className="text-zinc-400 hover:text-white transition-colors">发布</Link>
          <Link href="/video" className="text-zinc-400 hover:text-white transition-colors">视频号</Link>
        </nav>
        <main className="flex-1 p-6">{children}</main>
      </body>
    </html>
  );
}
