# Lecture 16 - Jobs, CronJobs & the work-queue pattern

## Answers to the lab checkpoints
- **(0)** **3 at a time, 6 in total.** `parallelism: 3` caps how many pods run concurrently; `completions: 6` is how many must *succeed* before the Job is `Complete`. With all-first-try success that's exactly 6 pods, three at a time, in two waves. (Pods only get re-created on *failure* - success never adds pods beyond `completions`.)
- **(1)** You see at most three `parallel-pi-*` pods Running at once; as each `Completed`, the Job controller starts another to keep heading toward 6. `kubectl get job parallel-pi` ends at `COMPLETIONS 6/6` with a `Complete` condition. Six pods total, all `Completed`.
- **(2)** Indices **0,1,2,3,4,5**, each **exactly once**. Indexed mode hands each completion a fixed slot in `JOB_COMPLETION_INDEX`; the controller drives every index to one success. No two pods share an index on success, and none is skipped - that's the whole point of static sharding.
- **(3)** **Fewer than one per minute - about one new Job every ~2 minutes. Each run takes ~90s, so when the next minute's fire is due the previous run is still active; under `Forbid` the controller skips** that occurrence rather than running two at once. The `describe cronjob` events record the skip.
- **(4)** The completed `slow-tick-*` Job (and its pod) vanishes ~120s after finishing, without you deleting it. `ttlSecondsAfterFinished` arms the TTL-after-finished controller to garbage-collect the finished Job. Without it, every minute's run would accumulate forever (bounded only by the history limits, which keep just a few).
- **(5)** **4 worker Jobs**, one per ConfigMap line. The manager reads the `work-items` ConfigMap (the *queue source*), loops over the items, and `kubectl apply`s a Job per item whose **only** per-item difference is the `ITEM` env var. The worker image is generic busybox - that separation (generic worker + pluggable queue source) is the DDS Work Queue shape. Each Job ends `1/1`.
- **(B1)** **5 pods, then `Failed`.** `restartPolicy: Never` means each failure creates a *new* pod; `backoffLimit: 3` allows 3 retries after the first attempt -> up to 4 failed pods before the Job gives up (you may see 4-5 depending on counting), each spaced by exponential backoff (~10s, 20s, 40s, capped at 6 min). `kubectl get job always-fails` ends `Failed` with `reason: BackoffLimitExceeded`. It never loops forever.
- **(B2)** Two failures: (1) you grind through hours of backlog while *new* work waits behind it (latency blows up, you may never catch up), and (2) you act on **stale items - recomputing a 14:00 price at 17:00, sending a now-irrelevant alert. The fix is freshness-aware processing: attach an enqueue timestamp / deadline to each item, drop or skip items older than a threshold, and consider draining newest-first during recovery. This only works if your workers are idempotent** so re-running or skipping is safe.

---

## What just happened (under the hood)
A **Job is a controller that runs pods until a target number of them succeed**. The pieces you exercised:

- **`completions` + `parallelism`.** `completions` is the success target; `parallelism` is the concurrency cap. The Job controller keeps `min(parallelism, remaining)` pods running, replacing each *successful* one only if more completions are still needed, and replacing each *failed* one (subject to `backoffLimit`).
- **`restartPolicy`. Jobs forbid `Always`. `Never` = a failed container's pod is left as `Failed` and a brand-new pod is created for the retry (you get a trail of failed pods - great for forensics). `OnFailure` = the kubelet restarts the container in place** in the same pod (fewer objects, but you lose the per-attempt pod history). Pick `Never` when you want to inspect each failed attempt; `OnFailure` when you want fewer objects.
- **`backoffLimit`. Bounds total retries. Failures are retried with exponential backoff** (10s, 20s, 40s ... capped at 6 minutes). Exhaust it and the Job gets a `Failed` condition with `reason: BackoffLimitExceeded` - it stops, it does not retry forever. (`activeDeadlineSeconds` is the orthogonal wall clock: it fails a Job that runs too *long* regardless of retries - your guard against a Job that never completes.)
- **Indexed mode. With `completionMode: Indexed`, each completion is bound to a stable index `0..completions-1`, exposed as `JOB_COMPLETION_INDEX` (and in the pod hostname). Each index must succeed once. This lets pods partition work with zero coordination** - index *is* the partition key. No leader, no shared lock, no queue: pod 3 always handles shard 3.
- CronJob = a Job factory on a schedule. It creates a new Job each time the cron `schedule` fires. `concurrencyPolicy` governs overlap: `Allow` (default - runs pile on top of each other), `Forbid` (skip the new run if the old is still active - what you saw), `Replace` (kill the old run, start the new). `startingDeadlineSeconds` says "if I missed the scheduled time by more than this, skip rather than fire late" - important after the controller was down. `successfulJobsHistoryLimit`/`failedJobsHistoryLimit` cap how many finished Jobs the CronJob keeps as history.
- **TTL after finished.** `ttlSecondsAfterFinished` on a Job arms a separate controller to delete the Job (and its pods) N seconds after it finishes - independent of CronJob history limits, and the only automatic cleanup for *standalone* Jobs.
- **The work-queue pattern (DDS).** A **manager** reads a **queue source (here a ConfigMap; in lab 17 a real queue) and creates one generic worker** Job per item. The worker doesn't know about the queue - it just gets its item via env/args. Swap the source without touching the worker.

The semantics that ties it all together: **at-least-once**. A node dying mid-run, a lost status update, or a retry can cause the *same* item to be processed **more than once. Kubernetes does not promise exactly-once. Therefore workers must be idempotent** - running them twice must be safe.

## Dev notes
- **Write idempotent workers.** At-least-once means duplicates *will* happen. Use upserts keyed by item id (not blind inserts), make file writes atomic (write temp, rename), and guard side effects (don't double-send an email) with a dedup key or a "already done?" check. The cheapest insurance in batch land.
- Make work re-runnable and checkpoint progress. A long worker that crashes at 90% should resume, not restart from zero. Persist a cursor/offset so a re-run skips finished sub-work.
- **Read your index, don't compute it.** In Indexed Jobs derive your slice from `JOB_COMPLETION_INDEX` (e.g. `rows[index*chunk : (index+1)*chunk]`). Don't hash hostnames or invent your own coordination.
- Choose `restartPolicy` deliberately. `Never` keeps failed pods around for `kubectl logs` per attempt; `OnFailure` keeps object count low. For debugging a flaky worker, `Never` is your friend.

## DevOps / Platform notes
- **Clean up or drown.** Completed Jobs and their pods linger forever by default. Always set `ttlSecondsAfterFinished` on standalone Jobs and tune `successful/failedJobsHistoryLimit` on CronJobs, or `kubectl get pods` becomes thousands of `Completed` rows and etcd bloats. This is the single most common batch hygiene miss.
- Missed schedules and `startingDeadlineSeconds`. If the CronJob controller is down (control-plane upgrade, etc.) past a fire time, it may try to "catch up." `startingDeadlineSeconds` bounds how late a run may start; too small and you silently drop runs, too large (or unset) and you get a thundering catch-up after an outage. Set it intentionally.
- **Timezones.** Classic CronJobs evaluate `schedule` in the controller's timezone (historically UTC). Newer `spec.timeZone` lets you pin it. Don't assume local time.
- **Label everything** so `kubectl get jobs -l app.kubernetes.io/name=...` and dashboards can find a batch family. The auto-injected `batch.kubernetes.io/job-name` and `controller-uid` labels link pods to their Job.

## Architect notes (trade-offs)
- Reusable work-queue vs bespoke Jobs. A generic worker + pluggable queue source (DDS) scales to many job types: you maintain one worker image and one manager, and add work by enqueuing. Bespoke per-task Jobs are simpler for one-off pipelines but multiply maintenance. Choose the queue when the *kinds* of work are uniform and the *volume/source* varies.
- Indexed Jobs (static sharding) vs a real queue (dynamic work). Indexed mode is perfect when the work set is **known and fixed at submit time (N shards of a dataset) - no broker, no coordination, deterministic. A real queue (Kafka/SQS - lab 17) wins when work arrives continuously**, items vary wildly in cost, or you need dynamic load-balancing and backpressure. Static sharding can hot-spot if shards are uneven; a queue self-balances but adds a broker dependency.
- **Job vs CronJob vs Deployment.** Jobs are for work that *ends*; Deployments for work that *runs forever*. A "job" that should run continuously is a smell - use a Deployment. A CronJob is just a scheduled Job factory; if you find yourself wanting a queue consumer that's always on, that's a Deployment (lab 17), not a per-minute CronJob.

## SRE notes (failure modes, SLOs, toil)
- **Stuck / never-completing Jobs.** A worker that hangs (waiting on a dead dependency) sits forever. `activeDeadlineSeconds` is your dead-man's switch - it fails the Job on a wall-clock budget. Alert on Jobs `Active` longer than expected.
- **Duplicated work. At-least-once + node failure -> an item can run twice. If you see double-writes, the bug is a non-idempotent worker**, not Kubernetes. Idempotency is the SLO-preserving design, not an optimization.
- A CronJob that silently stopped firing. The scariest batch failure is *silence* - the nightly run just... stopped, and nobody noticed for a week. Monitor `status.lastScheduleTime` / last-success time and alert when it's older than `interval × 2`. Absence of a Job is harder to see than a failing one.
- Processing obsolete work after a backlog. After an outage your queue holds hours of now-stale items. A naive worker drains oldest-first and grinds through all of it, possibly acting on outdated data while fresh work starves. Triage: tag items with an enqueue time, drop stale ones past a freshness deadline, and during recovery favor **newest-first**. (You reasoned about this in Break-it B2.)
- **Backoff masking permanent failure.** A high `backoffLimit` on a worker that's *permanently* broken (bad config, missing secret) just retries a doomed task for an hour. Keep `backoffLimit` modest and alert on `BackoffLimitExceeded` so a permanent failure pages fast instead of hiding behind retries.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- Offline / batch inference and embedding generation as Indexed Jobs. Shard the dataset into N partitions; each `JOB_COMPLETION_INDEX` processes one shard exactly once - no coordinator, deterministic coverage. Re-embedding a corpus, scoring a benchmark set, or backfilling features all map cleanly to Indexed Jobs.
- Idempotency so a re-run doesn't double-write embeddings. Key each vector by `(doc_id, model, version)` and upsert. At-least-once means a node failure can re-run a shard; an idempotent upsert makes that a no-op instead of duplicate rows in your vector DB.
- Nightly eval / benchmark runs as CronJobs. Quality regression suites, golden-set evals, and drift checks fit the Periodic Job pattern - schedule them, cap history, alert on `lastSuccessTime` going stale (a silently-stopped eval CronJob is how a quality regression ships unnoticed).
- Fine-tune / data-prep pipelines as Jobs. Each stage (clean -> tokenize -> shard -> train-prep) is a finite Job; chain them (lab 17's coordinated batch). All CPU-conceptual here - none of this lab needs a GPU; the *shape* is identical when the worker happens to request `nvidia.com/gpu`.

## Pitfalls
- **Non-idempotent workers** - at-least-once bites: duplicates, double-writes, double-sends. The number-one batch bug.
- **Missing TTL / history limits** -> Job and pod pileup until etcd and `kubectl get` choke. Set `ttlSecondsAfterFinished` and the CronJob history limits.
- **`backoffLimit` too high** -> masks a permanent failure as endless retries; a broken job looks "still trying" instead of paging you.
- CronJob `concurrencyPolicy` default is `Allow` -> a slow job whose runs overlap will **pile on** and saturate the cluster. For anything that can run longer than its interval, set `Forbid` or `Replace` deliberately.
- **Treating a Job as long-running** -> Jobs are for work that ends; use a Deployment for always-on consumers.

## Further reading
- **KP "Batch Job"** and **"Periodic Job"** - the pattern shapes for Jobs and CronJobs.
- **KIA ch4 - ReplicationControllers, ReplicaSets, DaemonSets, Jobs and CronJobs** mechanics.
- **DDS "Work Queue"** (and the multi-worker chapter) - the generic-worker + queue-source pattern you built; lab 17 swaps the ConfigMap for a real event source.
