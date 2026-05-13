# Lab 17 - Event-driven & coordinated batch · **Solution**
**Patterns: Scatter/Gather + Event-Driven + Coordinated Batch Source:** DDS ch8/12/13 **Est:** 55 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
```bash
kubectl create namespace lab-17-event-driven-batch
kubens lab-17-event-driven-batch        # or add -n lab-17-event-driven-batch to every command
```
**Predict (0): Three leaf Services are about to back the scatter/gather. Two are fast (`agnhost`), one is slow (`httpbin /delay/3`). Before you measure anything: if the root calls all three in parallel** and waits for all, roughly what wall-clock do you expect - closer to the *sum* of the three, or to the *slowest one*?

---

## Steps

### 1. Deploy the leaves (one of them is slow)
```bash
kubectl apply -f manifests/leaves.yaml
kubectl wait --for=condition=available deploy/leaf-1 deploy/leaf-2 deploy/leaf-3 --timeout=120s
kubectl get pods -l app.kubernetes.io/part-of=k8s-sre-course
```
**Observe (1):** Three Deployments + Services. `leaf-1`/`leaf-2` are `agnhost` (instant `/hostname`); `leaf-3` is `httpbin`, reachable at `http://leaf-3:8080/delay/3` (sleeps 3s, then 200). All three are exposed on port `8080` by Service name - that name is the only address the root knows.

### 2. Deploy the root and time a SEQUENTIAL gather (latency = sum)
```bash
kubectl apply -f manifests/scatter-gather.yaml
kubectl wait --for=condition=Ready pod/scatter-root --timeout=60s
kubectl exec scatter-root -- /scripts/seq.sh
```
**Predict (2):** `seq.sh` calls leaf-1, then leaf-2, then leaf-3 (the 3s one) one after another. What `WALL_CLOCK_SECONDS` do you expect - and which single leaf accounts for almost all of it?

### 3. Now the PARALLEL scatter/gather (latency = slowest leaf)
```bash
kubectl exec scatter-root -- /scripts/parallel.sh
```
**Prove it (3): Compare `WALL_CLOCK_SECONDS` from Step 3 against Step 2. Convince yourself the parallel run is ~3s (the slow leaf alone) while the sequential run was ~3s + the rest - i.e. parallel `<` sum, and parallel `≈` the slowest leaf. Run each twice; the numbers are stable. This is the headline: fan-out trades the sum for the max.**

### 4. MAP stage - an Indexed Job writes N partials to shared storage
> Needs an RWX StorageClass. Edit `storageClassName` in `manifests/shared-pvc.yaml` to your class (see **Cloud specifics**) **before** applying.
```bash
kubectl apply -f manifests/shared-pvc.yaml
kubectl apply -f manifests/map-job.yaml
kubectl wait --for=condition=complete job/wordcount-map --timeout=180s
kubectl logs -l app.kubernetes.io/name=wordcount-map --tail=20
```
**Predict (4): The map Job is `completionMode: Indexed`, `completions: 3`, `parallelism: 3`. Each worker counts the words in its** shard (`JOB_COMPLETION_INDEX` = 0/1/2) and writes `part-<idx>.txt` to the shared PVC. How many `part-*.txt` files should exist on the volume after the Job completes - and does any single mapper know the others' counts?

### 5. REDUCE stage - the BARRIER, then the correct final answer
```bash
kubectl apply -f manifests/reduce-job.yaml
kubectl wait --for=condition=complete job/wordcount-reduce --timeout=120s
kubectl logs -l app.kubernetes.io/name=wordcount-reduce --tail=20
```
**Prove it (5): The reducer prints `FINAL_WORD_COUNT=60`. The corpus is 3 shards × 4 lines × 5 words = 60 words** by construction, so 60 is the *known-correct* answer. Note in the log that the reducer first **waited** for all 3 partials (the barrier) and then summed them: `20 + 20 + 20 = 60`.

**Observe (5):** Read the value straight off the shared volume from the reducer pod's mount - it persisted the result:
```bash
POD=$(kubectl get pods -l app.kubernetes.io/name=wordcount-reduce -o jsonpath='{.items[0].metadata.name}')
kubectl logs "$POD" | grep FINAL_WORD_COUNT
```

### 6. (Optional, KEDA) scale a worker by QUEUE DEPTH
> Skip cleanly if KEDA is not installed. This file is here to **read** as much as to run - it has no broker behind it in this lab.
```bash
# Only if KEDA is installed (helm install keda kedacore/keda -n keda ...):
kubectl apply -f manifests/keda-scaledobject.yaml
kubectl get scaledobject queue-worker-scaler
kubectl describe scaledobject queue-worker-scaler | sed -n '/Triggers/,$p'
```
**Observe (6): The ScaledObject's trigger is `rabbitmq` `QueueLength`, `minReplicaCount: 0` (scale-to-zero), `maxReplicaCount: 20` (the ceiling = your backpressure guard). It scales on messages waiting**, not CPU. With no broker it will report an error on the trigger - that is expected; note *what* it scales on and that it can scale to **zero** when idle.

---

## Verify
```bash
# Scatter/gather: parallel wall-clock ~= slowest leaf, and < the sequential sum.
kubectl exec scatter-root -- /scripts/seq.sh      | grep WALL_CLOCK_SECONDS   # ~3 + a bit
kubectl exec scatter-root -- /scripts/parallel.sh | grep WALL_CLOCK_SECONDS   # ~3 (slowest leaf)

# Map produced N partials BEFORE reduce ran:
kubectl logs -l app.kubernetes.io/name=wordcount-map | grep -c 'wrote .* words'   # -> 3

# Reduce output is CORRECT (known answer = 60):
kubectl logs -l app.kubernetes.io/name=wordcount-reduce | grep FINAL_WORD_COUNT   # -> 60
```
yes Success = parallel `<` sequential and parallel `≈` slowest leaf; exactly **3** map partials; reduce prints `FINAL_WORD_COUNT=60`.

---

## Break it - one slow leaf dominates everything (the tail-latency lesson)

### B1 - make the slow leaf MUCH slower; watch it swallow the whole fan-out
Bump the delay and re-run the unbounded parallel gather:
```bash
# Raise leaf-3's delay to 10s for this scatter-root (overrides the ConfigMap value):
kubectl set env pod/scatter-root SLOW_DELAY=10 2>/dev/null || true
# (set env can't patch a bare Pod's running container; just call the slow path directly:)
kubectl exec scatter-root -- sh -c 'SLOW_DELAY=10 /scripts/parallel.sh' | grep WALL_CLOCK_SECONDS
```
**Predict (B1): Two of three leaves answer in milliseconds; one now takes 10s. The parallel gather `wait`s for all** of them. What does `WALL_CLOCK_SECONDS` become - the average (~3s), or pinned to the slow leaf (~10s)?

**Observe (B1):** Wall-clock is **~10s. The two fast leaves are irrelevant; a single slow shard set the latency of the entire request.** Averages lie here - your users feel the tail.

### B2 - the fix: a per-leaf TIMEOUT + partial result bounds it
```bash
kubectl exec scatter-root -- sh -c 'SLOW_DELAY=10 LEAF_TIMEOUT=2 /scripts/timeout.sh' | grep -E 'WALL_CLOCK_SECONDS|answered'
```
**Prove it (B2): `timeout.sh` wraps each leaf call in `timeout 2`; a leaf that misses its budget is dropped and the root returns what it has. `WALL_CLOCK_SECONDS` is now ~2s regardless of how slow leaf-3 gets, and the log shows `2/3 leaves answered in time`. Before:** one hang = total hang (~10s). **After:** latency bounded by the timeout, slow leaf served as a partial. That is the whole tail-latency discipline in two scripts.

---

## Cloud specifics

### EKS
- **RWX (Steps 4-5):** install the **EFS CSI driver, create an EFS filesystem + mount targets in the cluster's VPC, and apply an `efs-rwx` StorageClass (see `../00-cluster-setup/eks.md`). Set `shared-pvc.yaml`'s `storageClassName: efs-rwx`. EFS + mount targets are billed** - delete after.
- **KEDA (Step 6):** `helm repo add kedacore https://kedacore.github.io/charts && helm install keda kedacore/keda -n keda --create-namespace --version 2.14.0`. Same chart as OVH.

### OVH
- **RWX (Steps 4-5):** OVH MKS has **no native RWX block storage. Deploy `nfs-subdir-external-provisioner` (Helm) or use OVH NAS-HA mounted via NFS, then point `shared-pvc.yaml`'s `storageClassName` at that class (see `../00-cluster-setup/ovh.md`). The NFS/NAS share is billed**.
- **KEDA (Step 6):** identical Helm chart and command as EKS above.

---

## Cleanup
```bash
# Delete the shared RWX PVC explicitly so its backing share is released:
kubectl delete pvc mapreduce-shared -n lab-17-event-driven-batch --ignore-not-found
kubectl delete namespace lab-17-event-driven-batch
# If you installed KEDA only for this lab and want it gone:
# helm uninstall keda -n keda && kubectl delete namespace keda
```
> **Confirm the share is gone. Deleting the namespace deletes the pods and the PVC object, but an EFS filesystem / NFS export / NAS-HA share** lives outside the cluster and **keeps billing** until you delete it in the cloud console. Verify no lab share remains.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
