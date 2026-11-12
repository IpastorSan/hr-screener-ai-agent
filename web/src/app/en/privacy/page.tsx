import type { Metadata } from "next";
import { privacyAlternates } from "@/components/careers/metadata";
import { privacyEn } from "@/components/careers/privacy-en";
import { PrivacyNotice } from "@/components/careers/privacy-notice";

export const metadata: Metadata = {
  title: "Privacy notice",
  description:
    "How Grupo Sazón handles delivery driver applicants' data in Spain (GDPR) and Mexico (LFPDPPP), and how Ana, the AI screening assistant, works.",
  alternates: { canonical: "/en/privacy", ...privacyAlternates },
};

export default function PrivacyPage() {
  return <PrivacyNotice content={privacyEn} />;
}
