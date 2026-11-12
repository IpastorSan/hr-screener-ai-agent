import { JobPosting } from "@/components/careers/job-posting";
import { postingMetadata } from "@/components/careers/metadata";
import { POSTINGS } from "@/components/careers/postings";

const posting = POSTINGS.es.ES;

export const metadata = postingMetadata(posting);

export default function EmpleoRepartidorES() {
  return <JobPosting posting={posting} />;
}
