# Lab 16 - Jobs, CronJobs & work queue · **Solution**
**Patterns: Batch Job + Periodic Job + Work Queue Source: KIA 4; KP "Batch Job"/"Periodic Job"; DDS "Work Queue" Est:** 55 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`).
- No metrics-server or cloud add-ons needed. Jobs are cloud-agnostic.

## Setup
```bash
kubectl create namespace lab-16-jobs-cronjobs
kubens lab-16-jobs-cronjobs          # or add -n lab-16-jobs-cronjobs to every command
```
**Predict (0):** A Job sets `completions: 6` and `parallelism: 3`. How many pods will run *at the same time*, and how many pods will run *in total* if every pod succeeds on its first try?

---

## Steps

### 1. A parallel Job - fixed completions under a parallelism cap
```bash
kubectl apply -f manifests/job-parallel.yaml
kubectl get pods -l app.kubernetes.io/name=parallel-pi -w   # watch; Ctrl-C after all Completed
```
**Observe (1):** Watch the pod count. At any instant, how many `parallel-pi-*` pods are in `Running`/`ContainerCreating` at once? As each finishes, a fresh one starts until the total hits 6.

```bash
kubectl get job parallel-pi
```
**Prove it (1):** `kubectl get job parallel-pi` -> `COMPLETIONS` reads `6/6` and `STATUS`/`COMPLETE` is set. Confirm exactly 6 pods exist and all are `Completed`:
```bash
kubectl get pods -l app.kubernetes.io/name=parallel-pi
```

### 2. An Indexed Job - each pod owns a distinct shard
```bash
kubectl apply -f manifests/job-indexed.yaml
kubectl get pods -l app.kubernetes.io/name=indexed-shards -w   # Ctrl-C when all Completed
```
**Predict (2):** This Job is `completionMode: Indexed` with `completions: 6`. Each pod reads `JOB_COMPLETION_INDEX`. Before you read the logs - which index values do you expect to see, and how many times should each appear?

**Prove it (2):** Read every pod's log and collect the indices it processed:
```bash
kubectl logs -l app.kubernetes.io/name=indexed-shards --tail=-1 --prefix \
  | grep processing | sort
```
Convince yourself indices **0,1,2,3,4,5** each appear **exactly once** - no gaps, no duplicates. That is static sharding with zero coordination between pods.

### 3. A CronJob that fires every minute and skips overlaps (`Forbid`)
```bash
kubectl apply -f manifests/cronjob-forbid.yaml
kubectl get cronjob slow-tick
```
The job body `sleep 90` runs **longer** than the 1-minute schedule, so the next fire overlaps the previous run.

**Predict (3):** `schedule: "* * * * *"` (every minute) but each run takes ~90s, under `concurrencyPolicy: Forbid`. Over 3 minutes, roughly how many Jobs get created - one per minute, or fewer?

Watch for ~3 minutes:
```bash
kubectl get jobs -l app.kubernetes.io/name=slow-tick -w   # watch new Jobs appear; Ctrl-C after ~3 min
```
**Observe (3):** Count how many `slow-tick-*` Jobs were actually created over 3 minutes. Then read the controller's own account of the skip:
```bash
kubectl describe cronjob slow-tick | sed -n '/Events:/,$p'
```
Find an event mentioning that it `Cannot determine if job needs to be started` / missed or skipped a run because the prior one was still active.

### 4. `ttlSecondsAfterFinished` auto-deletes finished Jobs
The CronJob's `jobTemplate` sets `ttlSecondsAfterFinished: 120`.
```bash
kubectl get jobs -l app.kubernetes.io/name=slow-tick
```
**Observe (4):** Note a Job that has `COMPLETIONS 1/1`. Wait ~2 minutes past its completion, then re-list:
```bash
sleep 130
kubectl get jobs -l app.kubernetes.io/name=slow-tick
```
**Prove it (4):** A finished Job (and its pod) has **disappeared** on its own - you didn't delete it. The TTL controller reaped it 120s after it finished.

### 5. Work queue - a manager creates one worker Job per item
```bash
kubectl apply -f manifests/queue-configmap.yaml   # the "queue source": 4 items
kubectl apply -f manifests/queue-rbac.yaml         # SA + Role + RoleBinding (create Jobs)
kubectl apply -f manifests/queue-manager.yaml      # bitnami/kubectl pod that spawns Jobs
kubectl logs -f queue-manager                      # watch it create one Job per item
```
**Predict (5):** The ConfigMap `work-items` lists 4 items. How many worker Jobs should the manager create, and how does it derive the per-item difference between them?

**Prove it (5):** After the manager exits, list the Jobs it created and their completions:
```bash
kubectl get jobs -l app.kubernetes.io/name=work-queue
kubectl logs -l app.kubernetes.io/name=work-queue,batch.kubernetes.io/job-name --tail=-1 --prefix 2>/dev/null \
  || kubectl logs -l app.kubernetes.io/name=work-queue --tail=-1 --prefix
```
There should be **4** `worker-invoice-*` Jobs, each `1/1`, each logging `processing` then `DONE` for its own item.

---

## Verify
```bash
# Parallel Job reached 6 completions:
kubectl get job parallel-pi -o jsonpath='{.status.succeeded}/{.spec.completions}'; echo   # -> 6/6

# Indexed Job: 6 distinct indices, each once (count distinct == 6):
kubectl logs -l app.kubernetes.io/name=indexed-shards --tail=-1 | grep -o 'item-[0-5]' | sort -u | wc -l   # -> 6

# CronJob exists and is scheduling:
kubectl get cronjob slow-tick -o jsonpath='LAST_SCHEDULE={.status.lastScheduleTime}'; echo

# Overlaps were skipped: far fewer Jobs than minutes elapsed (Forbid working).
kubectl get jobs -l app.kubernetes.io/name=slow-tick

# Work queue created one Job per ConfigMap item:
kubectl get jobs -l app.kubernetes.io/name=work-queue --no-headers | wc -l   # -> 4
```
yes Success = `parallel-pi` shows `6/6`; the Indexed Job logged six distinct indices once each; the CronJob created fewer Jobs than minutes elapsed (overlaps skipped) and finished Jobs disappeared after TTL; the manager created 4 worker Jobs.

---

## Break it - exhaust the backoff limit, then think about stale work

### B1 - A worker that always fails -> `backoffLimit` exhausted -> `Failed`
```bash
kubectl apply -f manifests/job-failing.yaml
kubectl get pods -l app.kubernetes.io/name=always-fails -w   # watch new pods spawn with growing gaps; Ctrl-C after ~2 min
```
**Predict (B1):** The container does `exit 1` every time, with `backoffLimit: 3` and `restartPolicy: Never`. How many pods will be created, and what will `kubectl get job always-fails` finally report?

**Observe (B1):** Watch the *time gap* between successive pod creations - it grows (exponential backoff: ~10s, 20s, 40s...). Then read the final verdict:
```bash
kubectl get job always-fails
kubectl get job always-fails -o jsonpath='{.status.conditions[?(@.type=="Failed")].reason}'; echo
kubectl describe job always-fails | sed -n '/Events:/,$p'
```
**Prove it (B1): The Job ends `Failed` with reason `BackoffLimitExceeded`, and the count of failed pods equals the limit (`backoffLimit` + the original attempt). It does not** retry forever.

### B2 - Processing obsolete work (reason about it; don't run an outage)
Imagine the work queue (step 5) backed a real queue and your workers were **down for 3 hours. When they come back, the backlog still contains every item enqueued during the outage - some of which is now stale** (e.g. a "refresh price for 14:00" job at 17:00).
**Predict (B2):** A naive worker drains the queue oldest-first and processes *everything*. What two things go wrong, and what's the fix (hint: ordering + a freshness check)? Inspect what a naive replay would touch:
```bash
kubectl get configmap work-items -o jsonpath='{.data.items}'; echo
# Every line would be reprocessed on a blind replay -- including any now-stale item.
```

---

## Cleanup
```bash
kubectl delete namespace lab-16-jobs-cronjobs
```
No cloud LB/volume was created in this lab - deleting the namespace removes the Jobs, CronJob, manager pod, SA/Role/RoleBinding and ConfigMap.

### EKS
- No cloud-specific steps. Jobs/CronJobs are cloud-agnostic control-plane objects; worker pods schedule on any node. The only EKS nuance is that the `bitnami/kubectl` manager pod talks to the API server via its SA token - the same in-cluster auth on any cluster.

### OVH
- No cloud-specific steps. Same semantics on OVH MKS. Confirm the manager pod's SA can create Jobs with `kubectl auth can-i create jobs --as=system:serviceaccount:lab-16-jobs-cronjobs:queue-manager`.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
