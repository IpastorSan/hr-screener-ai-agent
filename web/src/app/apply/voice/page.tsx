import { Suspense } from "react";
import type { Metadata } from "next";
import { BrandMark } from "@/components/brand";
import { VoiceCallFromUrl } from "@/components/voice/voice-call";

export const metadata: Metadata = { title: "Entrevista por voz" };

export default function ApplyVoicePage() {
  return (
    <div className="flex h-dvh flex-col bg-background sm:items-center sm:justify-center sm:gap-4 sm:bg-muted sm:p-6">
      <BrandMark className="hidden sm:inline-flex" />
      <main className="flex min-h-0 w-full flex-1 flex-col sm:h-[min(860px,calc(100dvh-7rem))] sm:w-[420px] sm:flex-none sm:overflow-hidden sm:rounded-[28px] sm:border">
        <Suspense>
          <VoiceCallFromUrl />
        </Suspense>
      </main>
    </div>
  );
}
