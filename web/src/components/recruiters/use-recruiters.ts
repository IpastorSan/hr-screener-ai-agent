"use client";

import { listRecruiters } from "@/lib/recruiter-api";
import { cachedList } from "./cached-list";

const team = cachedList(() => listRecruiters());

/** The team from `GET /api/recruiters`, fetched once per page load; empty until loaded. */
export const useRecruiters = team.use;

/** Forget the cached team after an admin changes it. */
export const invalidateRecruiters = team.invalidate;
