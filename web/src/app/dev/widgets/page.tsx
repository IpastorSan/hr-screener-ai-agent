import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageShell } from "@/components/brand";
import { WidgetGallery } from "./widget-gallery";

export const metadata: Metadata = { title: "Widgets" };

export default function DevWidgetsPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return (
    <PageShell>
      <WidgetGallery />
    </PageShell>
  );
}
