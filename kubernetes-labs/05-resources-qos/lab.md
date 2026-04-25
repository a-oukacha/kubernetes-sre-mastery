# Lab 05 - Resource requests/limits, QoS & quotas · **Exercise**
**Patterns:** Predictable Demands **Source:** KIA 14; KP "Predictable Demands" **Est:** 50 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

## Objective
Make scheduling predictable and understand the cluster's guardrails: declare `requests`/`limits` deliberately, read the **QoS class** Kubernetes derives from them, *see* the difference between an OOMKill (memory, incompressible) and CPU throttling (compressible), and use `LimitRange` + `ResourceQuota` to default and cap a namespace.

## Concepts exercised
- `requests` vs `limits` (CPU in millicores, memory in Mi/Gi)
- the three **QoS classes**: Guaranteed, Burstable, BestEffort
- **OOMKill** (memory is incompressible) vs **CPU throttling** (CPU is compressible - capped, never killed)
- `kubectl top pod` (needs metrics-server)
- `LimitRange` - per-container defaults + min/max
- `ResourceQuota` - aggregate caps + the "requests become mandatory" rule
- eviction order under node memory pressure (BestEffort -> Burstable -> Guaranteed)

## Prerequisites
- Labs 01-02 done (kubectl fluency, `apply`/`describe`/`get -o jsonpath`).
- A reachable cluster (2-3 `Ready` nodes).
- **metrics-server installed** - confirm that node-level CPU/MEM metrics return real numbers rather than an error. See `../00-cluster-setup/tooling.md`.

## Setup
Create a namespace **`lab-05-resources-qos`** and make it the default for the rest of this lab (so you don't have to pass `-n` every time).

**Predict (0): You haven't applied any `LimitRange` or `ResourceQuota` yet. If you deploy a pod with no** `resources:` block right now, will it be admitted? What QoS class will it get? Check, and see if you were right.

---

## Tasks

### 1. Deploy one pod per QoS class
Apply the three provided manifests `manifests/pod-guaranteed.yaml`, `manifests/pod-burstable.yaml`, and `manifests/pod-besteffort.yaml`, then list the pods and confirm they are scheduled.

**Predict (1):** Open the three YAMLs. `qos-guaranteed` sets requests==limits; `qos-burstable` sets requests<limits; `qos-besteffort` sets nothing. *Before* you inspect anything, write down which QoS class each pod will get.

### 2. Read the QoS class Kubernetes derived
For each of the three pods, read the `status.qosClass` field Kubernetes assigned.

**Observe (2):** The class is **derived**, not declared - you never wrote `qosClass` anywhere. Which field combination produced each result? (Compare back to the three specs.)

### 3. Drive memory over the limit -> OOMKilled (incompressible)
Apply `manifests/pod-mem-oom.yaml` - `mem-hog` carries a 64Mi memory **limit** while `stress` inside it tries to allocate ~150Mi. Watch the pod's status and restart count over the first minute.

**Predict (3):** What `STATUS`/`RESTARTS` pattern will you see - will it ever stay Running?

Now read the cause from the pod object itself (not from the logs): inspect the last-terminated state - specifically the termination `reason` and `exitCode` in `status.containerStatuses[].lastState.terminated`.

**Observe (3):** What is the termination `Reason` and the exit code? (Memorize this pair - it's the OOM fingerprint.)

### 4. Drive CPU to the limit -> throttled, NOT killed (compressible)
Apply `manifests/pod-cpu-throttle.yaml` - `cpu-hog` runs `stress --cpu 2` (wants ~2000m) under a **200m** CPU limit. Note its restart count, give it ~20s, then read its live CPU usage via metrics-server.

**Predict (4):** Will it be killed like `mem-hog` was? What number will the CPU usage metric show?

**Prove it (4): It's throttled, not killed - prove it from the kernel's own throttling counters inside the container's CPU cgroup (`cpu.stat`). Read the counters, wait ~10s, read them again, and re-check the restart count. Convince yourself the throttling counters (`nr_throttled` and `throttled_usec` on cgroup v2, or `throttled_time` on v1) grow** between the two reads while `RESTARTS` stays `0`. (If your platform blocks reading the cgroup file, the CPU usage capping at ~200m plus 0 restarts is enough proof.)

### 5. (No-op for QoS, on purpose) confirm BestEffort still alive
Re-read the `status.qosClass` of `qos-besteffort`.

**Observe (5): Still `BestEffort`. Note it now - in Break it** it's the first to die under pressure.

### 6. Apply a LimitRange -> a pod with NO requests gets DEFAULTED
Apply `manifests/limitrange.yaml` and inspect the `lab-05-defaults` LimitRange so you know what defaults it sets. Then apply `manifests/pod-no-requests.yaml` - a pod that declares **no** `resources:` block at all - and read back both its effective `spec.containers[].resources` and its `status.qosClass`.

**Predict (6):** After the LimitRange exists, what requests/limits will the running pod actually have, and what QoS class?

**Prove it (6):** Confirm the running pod carries `requests`/`limits` values that you never wrote in the manifest - proof that defaulting happened at admission via the LimitRange.

### 7. Apply a ResourceQuota -> requests become MANDATORY, and aggregate caps bite
Apply `manifests/resourcequota.yaml`, then inspect the `lab-05-quota` ResourceQuota to see its USED vs HARD figures.

**Predict (7a):** With the quota in place, suppose you delete and re-create a truly request-less pod (assume no LimitRange defaulting). What happens at admission?

**Predict (7b):** `manifests/pod-quota-buster.yaml` is well-formed (it *has* requests) but asks for **800Mi of memory requests, while the quota caps `requests.memory` at 512Mi** namespace-wide. Attempt to apply it and expect a rejection.

**Observe (7):** Read the exact rejection message. Which resource did it exceed, and what were the USED / LIMITED / REQUESTED numbers in the error?

**Prove it (7):** Show the quota *would* reject a request-less pod too. Temporarily remove the defaulter (delete the LimitRange and the existing `no-requests` pod), then try to apply the request-less `manifests/pod-no-requests.yaml` again - expect it to fail for missing requests - and finally re-apply the LimitRange to restore defaulting. Confirm the error names the missing `requests.cpu`/`requests.memory` - proof that once a ResourceQuota exists, every container must declare requests (directly or via a LimitRange default).

---

## Verify
Demonstrate success with observable signals: `qos-guaranteed`'s `status.qosClass` reads `Guaranteed` and `qos-burstable`'s reads `Burstable`; `mem-hog`'s last-terminated state shows reason `OOMKilled` with exit code `137`; `cpu-hog` has `0` restarts; `no-requests` carries injected requests; and a fresh apply of the quota-buster prints an "exceeded quota" error.

yes Success = Guaranteed/Burstable classes correct, `mem-hog` shows `OOMKilled`/`137`, `cpu-hog` has 0 restarts, `no-requests` carries injected requests, and the quota-buster apply prints an "exceeded quota" error.

---

## Break it - eviction order + the OOM loop

### B1 - Memory limit below the working set = instant OOM loop
You already saw it: `mem-hog` never stabilizes. Confirm the loop is *the limit*, not the app, by giving it room: raise its memory limit to `256Mi` (above the ~150Mi working set) and watch whether it settles. (Pod resource fields are largely immutable, so if an in-place patch is rejected, edit the limit to `256Mi` in `manifests/pod-mem-oom.yaml`, delete `mem-hog`, re-apply, and observe.)

**Predict (B1):** With a 256Mi limit and a ~150Mi working set, does the OOM loop stop?

### B2 - Under node memory pressure, BestEffort dies first
**Do NOT crash your node.** Keep numbers small. The reliable, *safe* way to see eviction order is to read the kubelet's own ranking rather than actually starving a node: inspect the OOM score the kubelet assigned to each pod's main process (`/proc/1/oom_score_adj` from inside the container; higher = killed sooner) for the different QoS classes.

**Predict (B2):** Rank the three classes by `oom_score_adj` (and therefore eviction/OOM order). Which is killed *first* when the node runs low on memory, which is protected *last*?

**Observe (B2): If you ever see a real eviction in a busy cluster, it surfaces in the cluster events filtered for eviction/OOM entries. The kubelet evicts in order BestEffort -> Burstable -> Guaranteed**. (Eviction thresholds are cloud-agnostic; what differs per cloud is node *allocatable* - see EKS/OVH below.)

### EKS
- Node **allocatable** (schedulable memory after kube/system reserved + eviction threshold) depends on instance type. A `t3.large` (8 GiB) reserves a chunk for the kubelet/OS, so don't size requests to the raw instance memory.
- Default hard eviction signal is `memory.available<100Mi` (kubelet default); the *order* across QoS is identical to any cluster.

### OVH
- Same QoS/eviction semantics. Node allocatable varies by **flavor** (e.g. `b2-7` ≈ 7 GiB raw, less allocatable). Verify it by describing a node and reading its `Allocatable` block.
- Eviction thresholds are cloud-agnostic kubelet behavior; only the absolute numbers shift with the flavor.

---

## Cleanup
Delete the `lab-05-resources-qos` namespace. No cloud LB/volume was created here, so removing the namespace clears all pods, the LimitRange and the ResourceQuota.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
