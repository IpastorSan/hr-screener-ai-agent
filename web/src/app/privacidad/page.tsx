import type { Metadata } from "next";
import { privacyAlternates } from "@/components/careers/metadata";
import { privacyEs } from "@/components/careers/privacy-es";
import { PrivacyNotice } from "@/components/careers/privacy-notice";

export const metadata: Metadata = {
  title: "Aviso de privacidad",
  description:
    "Cómo trata Grupo Sazón los datos de los candidatos a repartidor/a en España (RGPD) y México (LFPDPPP), y cómo funciona Ana, la asistente de IA de selección.",
  alternates: { canonical: "/privacidad", ...privacyAlternates },
};

export default function PrivacidadPage() {
  return <PrivacyNotice content={privacyEs} />;
}
