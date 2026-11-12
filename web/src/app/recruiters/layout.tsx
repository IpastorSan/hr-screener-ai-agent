import { RecruiterHeader } from "@/components/recruiters/header";
import { SessionGate } from "@/components/recruiters/session";

export default function RecruitersLayout({ children, ask }: LayoutProps<"/recruiters">) {
  return (
    <SessionGate>
      <div className="flex min-h-full flex-1 flex-col">
        <RecruiterHeader ask={ask} />
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:py-8">{children}</main>
      </div>
    </SessionGate>
  );
}
