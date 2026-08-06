import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NIKU STDUIO FOR WORK | Local AI Film Production",
  description:
    "脚本、世界設定、参照素材、H3生成プロンプト、動画テイクをCodexと共同管理するローカル映像制作スタジオ。",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
