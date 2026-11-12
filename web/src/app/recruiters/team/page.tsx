import type { Metadata } from "next";
import { TeamAdmin } from "@/components/recruiters/team-admin";

export const metadata: Metadata = { title: "Reclutadores" };

export default function TeamPage() {
  return <TeamAdmin />;
}
