import type { Metadata } from "next";
import { POSTINGS, PRIVACY_PATHS, type Posting } from "./postings";

export function postingMetadata(posting: Posting): Metadata {
  return {
    title: posting.meta.title,
    description: posting.meta.description,
    alternates: {
      canonical: posting.path,
      languages: { es: POSTINGS.es[posting.country].path, en: POSTINGS.en[posting.country].path },
    },
    openGraph: { title: posting.meta.title, description: posting.meta.description, type: "website" },
  };
}

export const privacyAlternates = { languages: { es: PRIVACY_PATHS.es, en: PRIVACY_PATHS.en } };
