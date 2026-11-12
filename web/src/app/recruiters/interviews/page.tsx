import type { Metadata } from "next";
import { MyInterviews } from "@/components/recruiters/my-interviews";

export const metadata: Metadata = { title: "Mis entrevistas" };

export default function InterviewsPage() {
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <h1 className="font-heading text-2xl font-normal tracking-tight sm:text-3xl">Mis entrevistas</h1>
        <p className="text-sm text-muted-foreground">Las videollamadas que los candidatos reservaron con Ana y que te tocan a ti, y las entrevistas en persona de tus locales, por día.</p>
      </div>
      <MyInterviews />
    </div>
  );
}
