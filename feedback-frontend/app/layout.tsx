import type { Metadata } from "next";
import { Geist } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Course feedback · DotAI Labs AI Certificate",
  description: "Tell us how your DotAI Labs AI Certificate course went.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={geistSans.variable}>
      <body>
        <header className="site-header">
          <span className="brand">
            DotAI Labs <span className="brand-sub">AI Certificate</span>
          </span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
