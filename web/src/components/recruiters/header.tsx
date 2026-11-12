import { Suspense } from "react";
import { BrandMark } from "@/components/brand";
import { CalendarConnection } from "./calendar-connection";
import { RecruiterNav } from "./nav";
import { UserMenu } from "./user-menu";

/** Recruiter header. `ask` is the slot for the recruiter assistant (SAZ-15). On a phone the
 * navigation moves to a second row. */
export function RecruiterHeader({ ask }: { ask?: React.ReactNode }) {
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex h-14 w-full max-w-7xl items-center gap-3 px-4 sm:gap-6">
        <BrandMark className="shrink-0" />
        <div className="hidden min-w-0 md:block">
          <Suspense fallback={<RecruiterNav current={null} />}>
            <RecruiterNav />
          </Suspense>
        </div>
        <div data-slot="ask" className="ml-auto flex items-center gap-2">
          {ask}
          <Suspense>
            <CalendarConnection />
          </Suspense>
          <UserMenu />
        </div>
      </div>
      <div className="-mt-1 overflow-x-auto px-4 pb-2 md:hidden">
        <Suspense fallback={<RecruiterNav current={null} />}>
          <RecruiterNav />
        </Suspense>
      </div>
    </header>
  );
}
