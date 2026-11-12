"use client";

import { listLocations } from "@/lib/recruiter-api";
import { cachedList } from "./cached-list";

const locations = cachedList(() => listLocations());

/** The kitchens from `GET /api/locations`, fetched once per page load; empty until loaded. */
export const useLocations = locations.use;
