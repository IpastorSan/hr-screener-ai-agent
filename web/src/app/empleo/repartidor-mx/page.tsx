import { JobPosting } from "@/components/careers/job-posting";
import { postingMetadata } from "@/components/careers/metadata";
import { POSTINGS } from "@/components/careers/postings";

const posting = POSTINGS.es.MX;

export const metadata = postingMetadata(posting);

export default function EmpleoRepartidorMX() {
  return <JobPosting posting={posting} />;
}
