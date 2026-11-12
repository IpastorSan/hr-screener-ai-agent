import type { Metadata } from "next";
import { Suspense } from "react";
import { CandidateDetailView, DetailSkeleton } from "@/components/recruiters/candidate-detail";

export const metadata: Metadata = { title: "Candidato" };

async function Candidate({ params }: { params: PageProps<"/recruiters/[id]">["params"] }) {
  const { id } = await params;
  return <CandidateDetailView id={id} />;
}

export default function CandidatePage({ params }: PageProps<"/recruiters/[id]">) {
  return (
    <Suspense fallback={<DetailSkeleton />}>
      <Candidate params={params} />
    </Suspense>
  );
}
