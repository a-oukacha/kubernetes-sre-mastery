# Lab 16 - Jobs, CronJobs & work queue · **Exercise**
**Patterns: Batch Job + Periodic Job + Work Queue Source: KIA 4; KP "Batch Job"/"Periodic Job"; DDS "Work Queue" Est:** 55 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

## Objective
Run **finite** and **scheduled** work reliably, and build a simple **work queue**. You'll watch a Job drive pods to a fixed number of `completions` under a `parallelism` cap; give each pod a stable shard via Indexed mode; let a CronJob fire on a schedule and *skip* an overlapping run under `concurrencyPolicy: Forbid`; auto-clean finished Jobs with `ttlSecondsAfterFinished`; and have a manager pod create one worker Job per item from a queue source. Then exhaust a `backoffLimit` and see a Job marked `Failed`.

## Concepts exercised
- Job: `completions`, `parallelism`, `backoffLimit`, `restartPolicy: Never|OnFailure`, `activeDeadlineSeconds`
- Indexed Job: `completionMode: Indexed`, the `JOB_COMPLETION_INDEX` each pod gets
- CronJob: `schedule`, `concurrencyPolicy: Allow|Forbid|Replace`, `startingDeadlineSeconds`, `successfulJobsHistoryLimit`/`failedJobsHistoryLimit`, `ttlSecondsAfterFinished`
- Work-queue pattern: a manager creates one worker Job per queue item (generic worker + queue source)
- At-least-once semantics -> idempotent workers; `BackoffLimitExceeded`

## Prerequisites
- Labs 01-02 done (kubectl fluency, `apply`/`get`/`logs`/`-o jsonpath`).
- A reachable cluster (2-3 `Ready` nodes).
- No metrics-server or cloud add-ons needed. Jobs are cloud-agnostic.

## Setup
Create a namespace **`lab-16-jobs-cronjobs`** and make it the default for the rest of this lab (so you don't have to pass `-n` every time).

**Predict (0):** A Job sets `completions: 6` and `parallelism: 3`. How many pods will run *at the same time*, and how many pods will run *in total* if every pod succeeds on its first try?

---

## Tasks

### 1. A parallel Job - fixed completions under a parallelism cap
Apply `manifests/job-parallel.yaml` and watch the `parallel-pi-*` pods churn as the Job drives toward its `completions` target.

**Observe (1):** At any instant, how many `parallel-pi-*` pods are in `Running`/`ContainerCreating` at once? Convince yourself that as each finishes a fresh one starts until the total reaches 6.

**Prove it (1):** Show the Job's `COMPLETIONS` reads `6/6` and its complete status is set, and that exactly 6 pods exist, all `Completed`.

### 2. An Indexed Job - each pod owns a distinct shard
Apply `manifests/job-indexed.yaml` (it runs in `completionMode: Indexed` with `completions: 6`; each pod reads `JOB_COMPLETION_INDEX`) and let all pods complete.

**Predict (2):** Before you read the logs - which index values do you expect to see, and how many times should each appear?

**Prove it (2): Collect, from every pod's log, the index each one processed. Convince yourself indices 0,1,2,3,4,5** each appear **exactly once** - no gaps, no duplicates. That is static sharding with zero coordination between pods.

### 3. A CronJob that fires every minute and skips overlaps (`Forbid`)
Apply `manifests/cronjob-forbid.yaml` and confirm the CronJob exists. Its job body sleeps ~90s - **longer** than the 1-minute `schedule` - so each fire overlaps the previous run, and it carries `concurrencyPolicy: Forbid`.

**Predict (3):** `schedule: "* * * * *"` (every minute) but each run takes ~90s, under `concurrencyPolicy: Forbid`. Over 3 minutes, roughly how many Jobs get created - one per minute, or fewer?

**Observe (3):** Watch new Jobs appear for ~3 minutes and count how many `slow-tick-*` Jobs are actually created. Then read the CronJob's own Events and find the controller's account of the overlap - an event stating it could not start (or missed/skipped) a run because the prior one was still active.

### 4. `ttlSecondsAfterFinished` auto-deletes finished Jobs
The CronJob's `jobTemplate` sets `ttlSecondsAfterFinished: 120`.

**Observe (4):** Find a `slow-tick-*` Job that has reached `COMPLETIONS 1/1`. Note it, wait roughly 2 minutes past its completion, then list the Jobs again.

**Prove it (4): Confirm that the finished Job (and its pod) has disappeared** on its own - you never deleted it. The TTL controller reaped it 120s after it finished.

### 5. Work queue - a manager creates one worker Job per item
Apply the queue source `manifests/queue-configmap.yaml` (the `work-items` ConfigMap, 4 items), the access objects `manifests/queue-rbac.yaml` (a ServiceAccount + Role + RoleBinding granting Job creation), and `manifests/queue-manager.yaml` (a manager pod that spawns one worker Job per item). Follow the manager's logs as it runs.

**Predict (5):** The ConfigMap `work-items` lists 4 items. How many worker Jobs should the manager create, and how does it derive the per-item difference between them?

**Prove it (5): After the manager exits, list the Jobs it created with their completions and read the worker logs. There should be 4** `worker-invoice-*` Jobs, each `1/1`, each logging `processing` then `DONE` for its own item.

---

## Verify
Demonstrate success with observable signals: the `parallel-pi` Job's `status.succeeded`/`spec.completions` reads `6/6`; the Indexed Job logged six distinct indices, each once; the `slow-tick` CronJob has a recent `lastScheduleTime` yet created **fewer Jobs than minutes elapsed (overlaps skipped), and finished Jobs vanished after TTL; and the work queue produced exactly 4** `work-queue` Jobs (one per ConfigMap item).

yes Success = `parallel-pi` shows `6/6`; the Indexed Job logged six distinct indices once each; the CronJob created fewer Jobs than minutes elapsed (overlaps skipped) and finished Jobs disappeared after TTL; the manager created 4 worker Jobs.

---

## Break it - exhaust the backoff limit, then think about stale work

### B1 - A worker that always fails -> `backoffLimit` exhausted -> `Failed`
Apply `manifests/job-failing.yaml` (its container `exit 1`s every time, with `backoffLimit: 3` and `restartPolicy: Never`) and watch new `always-fails` pods spawn.

**Predict (B1):** How many pods will be created, and what will the `always-fails` Job finally report?

**Observe (B1):** Watch the *time gap* between successive pod creations and convince yourself it grows (exponential backoff: ~10s, 20s, 40s...). Then read the Job's final verdict: its status, the reason on its `Failed` condition, and its Events.

**Prove it (B1): The Job ends `Failed` with reason `BackoffLimitExceeded`, and the count of failed pods equals the limit (`backoffLimit` + the original attempt). It does not** retry forever.

### B2 - Processing obsolete work (reason about it; don't run an outage)
Imagine the work queue (task 5) backed a real queue and your workers were **down for 3 hours. When they come back, the backlog still contains every item enqueued during the outage - some of which is now stale** (e.g. a "refresh price for 14:00" job at 17:00).

**Predict (B2):** A naive worker drains the queue oldest-first and processes *everything*. What two things go wrong, and what's the fix (hint: ordering + a freshness check)? Inspect the `work-items` ConfigMap contents to picture exactly what a blind replay would reprocess - including any now-stale item.

---

## Cleanup
Delete the `lab-16-jobs-cronjobs` namespace. No cloud LB/volume was created here, so removing the namespace removes the Jobs, CronJob, manager pod, SA/Role/RoleBinding and ConfigMap.

### EKS
- No cloud-specific steps. Jobs/CronJobs are cloud-agnostic control-plane objects; worker pods schedule on any node. The only EKS nuance is that the `bitnami/kubectl` manager pod talks to the API server via its ServiceAccount token - the same in-cluster auth on any cluster.

### OVH
- No cloud-specific steps. Same semantics on OVH MKS. Confirm the manager pod's ServiceAccount is actually allowed to create Jobs in this namespace before relying on it.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
