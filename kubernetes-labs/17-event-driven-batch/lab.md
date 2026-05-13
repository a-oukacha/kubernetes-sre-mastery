# Lab 17 - Event-driven & coordinated batch · **Exercise**
**Patterns: Scatter/Gather + Event-Driven + Coordinated Batch Source:** DDS ch8/12/13 **Est:** 55 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations and the timings yourself; that *is* the skill. Attempt every task and write down
> your answer to every **Predict / Observe / Prove it** before peeking. When you're stuck or done,
> [`solution.md`](solution.md) has the exact commands + the numbers you should have seen + every
> checkpoint answer. Then read [`lecture.md`](lecture.md) for the course.

## Objective
Compose asynchronous pipelines and parallel compute, then feel the two facts that govern every fan-out system: scatter/gather total latency is the SLOWEST leaf, not the average (tail latency), and **coordinated batch needs a BARRIER** - reduce cannot start until every map finishes. You will fan a request out to N leaf services in parallel and prove the wall-clock matches the slowest leaf (and beats the sequential sum), run a map -> shuffle -> reduce word-count over shared storage and prove the answer is correct, then make one leaf hang and watch it dominate everything - until a per-leaf timeout + partial result bounds it.

## Concepts exercised
- **Scatter/Gather** - a root fans a request to N leaves in parallel and merges; total latency = the slowest leaf
- **Tail latency** - more leaves = higher chance one is slow = worse p99 of the fan-out
- **Event-driven chain** - stages decoupled by a queue/topic; scale workers by queue depth (KEDA, optional)
- **Coordinated batch - map (Indexed Job, parallelism=N) -> shuffle on shared storage -> reduce, with a barrier** between
- **Backpressure & dead-letter queues** (concept, lecture)
- per-call **timeouts** + **partial-result** handling as the tail-latency fix

## Prerequisites
- Labs **06 (Services/DNS - the leaves are reached by Service name), 09** (PV/PVC, **RWX**), **16** (Jobs, `parallelism`/`completions`, Indexed Jobs) done.
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`).
- For the map/reduce stage: an **RWX StorageClass (EKS `efs-rwx`; OVH NFS/NAS-HA). See Cloud specifics** below. The scatter/gather half needs no storage.
- **KEDA is OPTIONAL** - only Step 6 uses it; skip cleanly if not installed.

> **COST GUARD.** The map/reduce stage provisions a **real RWX volume** (EFS / NFS / NAS-HA), which is **billed**. Keep it at **1Gi** and run **Cleanup** the moment you finish - and confirm the share is gone.

## Setup
Create a namespace **`lab-17-event-driven-batch`** and make it the default for the rest of this lab (so you don't have to pass `-n` every time).

**Predict (0): Three leaf Services are about to back the scatter/gather. Two are fast (`agnhost`), one is slow (`httpbin /delay/3`). Before you measure anything: if the root calls all three in parallel** and waits for all, roughly what wall-clock do you expect - closer to the *sum* of the three, or to the *slowest one*?

---

## Tasks

### 1. Deploy the leaves (one of them is slow)
Apply `manifests/leaves.yaml`, wait until `leaf-1`, `leaf-2`, and `leaf-3` are available, then list the resulting pods. Three Deployments + Services come up: `leaf-1`/`leaf-2` are `agnhost` (instant `/hostname`); `leaf-3` is `httpbin`, reachable at `http://leaf-3:8080/delay/3`. All three are exposed on port `8080` by Service name.

**Observe (1):** Each leaf is addressed only by its Service name - that name is the only address the root knows. Confirm all three are up and note which one is the slow one.

### 2. Deploy the root and time a SEQUENTIAL gather (latency = sum)
Apply `manifests/scatter-gather.yaml`, wait for the `scatter-root` pod to be Ready, then run the sequential gather script (`/scripts/seq.sh`) inside it. It calls leaf-1, then leaf-2, then leaf-3 one after another. Record the reported `WALL_CLOCK_SECONDS`.

**Predict (2):** Before you run it - what `WALL_CLOCK_SECONDS` do you expect from the sequential gather, and which single leaf accounts for almost all of it?

### 3. Now the PARALLEL scatter/gather (latency = slowest leaf)
Run the parallel gather script (`/scripts/parallel.sh`) inside `scatter-root` and record its `WALL_CLOCK_SECONDS`.

**Prove it (3):** Compare the parallel run against the sequential run from Step 2. Run each twice for stable numbers. Convince yourself the parallel run tracks the *slowest leaf alone* while the sequential run was that plus the rest - i.e. parallel `<` sum, and parallel `≈` the slowest leaf. This is the headline: fan-out trades the sum for the max.

### 4. MAP stage - an Indexed Job writes N partials to shared storage
> Needs an RWX StorageClass. Edit `storageClassName` in `manifests/shared-pvc.yaml` to your class (see **Cloud specifics**) **before** applying.

Apply `manifests/shared-pvc.yaml`, then apply `manifests/map-job.yaml`, wait for the `wordcount-map` Job to complete, and read its pods' logs. The Job is `completionMode: Indexed`, `completions: 3`, `parallelism: 3`; each worker counts the words in its own shard (keyed by `JOB_COMPLETION_INDEX` = 0/1/2) and writes `part-<idx>.txt` to the shared PVC.

**Predict (4):** How many `part-*.txt` files should exist on the volume after the Job completes - and does any single mapper know the others' counts?

### 5. REDUCE stage - the BARRIER, then the correct final answer
Apply `manifests/reduce-job.yaml`, wait for the `wordcount-reduce` Job to complete, and read its logs. The corpus is 3 shards × 4 lines × 5 words = 60 words by construction, so the final count has a *known-correct* value.

**Prove it (5):** Show that the reducer first **waited** for all 3 partials (the barrier) before summing, and that the `FINAL_WORD_COUNT` it prints equals the known-correct total. Reason about why reduce cannot legally begin until every map partial exists.

**Observe (5): Read the `FINAL_WORD_COUNT` value straight off the shared volume via the reducer pod's mount/logs - prove the result was persisted** to shared storage, not just printed in passing.

### 6. (Optional, KEDA) scale a worker by QUEUE DEPTH
> Skip cleanly if KEDA is not installed. This file is here to **read** as much as to run - it has no broker behind it in this lab.

If (and only if) KEDA is installed, apply `manifests/keda-scaledobject.yaml`, then inspect the resulting `queue-worker-scaler` ScaledObject and its triggers.

**Observe (6):** From the ScaledObject, identify *what* metric it scales on (not CPU), the minimum and maximum replica counts, and which of those acts as your backpressure ceiling. Note what `minReplicaCount: 0` implies when the workload is idle. With no broker behind it, the trigger will report an error - explain why that is expected and harmless here.

---

## Verify
Demonstrate success with observable signals: the parallel scatter/gather wall-clock is `<` the sequential sum and `≈` the slowest leaf; the map stage produced exactly **3** partials *before* reduce ran; and the reduce stage's `FINAL_WORD_COUNT` matches the known-correct total.

yes Success = parallel `<` sequential and parallel `≈` slowest leaf; exactly **3** map partials; reduce prints the known-correct `FINAL_WORD_COUNT`.

---

## Break it - one slow leaf dominates everything (the tail-latency lesson)

### B1 - make the slow leaf MUCH slower; watch it swallow the whole fan-out
Re-run the unbounded parallel gather, but raise the slow leaf's delay well above the others (an env override for the slow path). The two fast leaves still answer in milliseconds; one now takes much longer. The parallel gather waits for **all** of them.

**Predict (B1):** What does `WALL_CLOCK_SECONDS` become - the *average* of the three, or pinned to the *slow* leaf?

**Observe (B1):** Measure it and reconcile against your prediction. What does this say about reporting averages versus tail latency for a fan-out request?

### B2 - the fix: a per-leaf TIMEOUT + partial result bounds it
Run the timeout variant of the gather (`/scripts/timeout.sh`) with both the raised slow-leaf delay **and** a per-leaf timeout budget set. Each leaf call is wrapped in a deadline; a leaf that misses its budget is dropped and the root returns what it has.

**Prove it (B2): Show that `WALL_CLOCK_SECONDS` is now bounded by the timeout budget regardless of how slow the slow leaf gets, and that the log reports how many of the three leaves answered in time. Contrast the before** (one hang = total hang) with the **after** (latency bounded, slow leaf served as a partial). That is the whole tail-latency discipline.

---

## Cloud specifics

### EKS
- **RWX (Steps 4-5):** install the **EFS CSI driver, create an EFS filesystem + mount targets in the cluster's VPC, and apply an `efs-rwx` StorageClass (see `../00-cluster-setup/eks.md`). Set `shared-pvc.yaml`'s `storageClassName: efs-rwx`. EFS + mount targets are billed** - delete after.
- **KEDA (Step 6):** install the KEDA Helm chart from the `kedacore` repo into a `keda` namespace (pin a recent 2.x version). Same chart as OVH.

### OVH
- **RWX (Steps 4-5):** OVH MKS has **no native RWX block storage. Deploy `nfs-subdir-external-provisioner` (Helm) or use OVH NAS-HA mounted via NFS, then point `shared-pvc.yaml`'s `storageClassName` at that class (see `../00-cluster-setup/ovh.md`). The NFS/NAS share is billed**.
- **KEDA (Step 6):** identical Helm chart and procedure as EKS above.

---

## Cleanup
Delete the shared RWX PVC (`mapreduce-shared`) explicitly so its backing share is released, then delete the `lab-17-event-driven-batch` namespace. If you installed KEDA only for this lab, uninstall it and remove its namespace too.

> **Confirm the share is gone. Deleting the namespace deletes the pods and the PVC object, but an EFS filesystem / NFS export / NAS-HA share** lives outside the cluster and **keeps billing** until you delete it in the cloud console. Verify no lab share remains.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
