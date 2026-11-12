import type { Metadata } from "next";
import { Fraunces, Lato } from "next/font/google";
import { Toaster } from "@/components/ui/sonner";
import "./globals.css";

const lato = Lato({ variable: "--font-lato", subsets: ["latin"], weight: ["400", "700"], style: ["normal", "italic"] });
const fraunces = Fraunces({ variable: "--font-fraunces", subsets: ["latin"], axes: ["opsz"], style: ["normal", "italic"] });

export const metadata: Metadata = {
  // Canonical and hreflang links must be absolute. Read at build time (web/Dockerfile).
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"),
  title: { default: "Grupo Sazón", template: "%s · Grupo Sazón" },
  description: "Selección de repartidores de Grupo Sazón",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es" className={`${lato.variable} ${fraunces.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">
        {children}
        <Toaster />
      </body>
    </html>
  );
}
