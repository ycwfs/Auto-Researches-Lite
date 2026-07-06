import { useEffect, useRef, useState } from "react";
import { api, type Job } from "./api";

// Polls a job until it reaches a terminal state. Returns the latest job.
export function useJobPolling(jobId: number | null, onDone?: () => void): Job | null {
  const [job, setJob] = useState<Job | null>(null);
  const doneRef = useRef(false);

  useEffect(() => {
    if (jobId == null) return;
    doneRef.current = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const j = await api.getJob(jobId);
        setJob(j);
        if (["succeeded", "failed", "canceled"].includes(j.status)) {
          if (!doneRef.current) {
            doneRef.current = true;
            onDone?.();
          }
          return;
        }
      } catch {
        /* keep polling */
      }
      timer = setTimeout(tick, 1200);
    };
    void tick();
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  return job;
}
