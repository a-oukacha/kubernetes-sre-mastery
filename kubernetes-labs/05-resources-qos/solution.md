# Lab 05 - Resource requests/limits, QoS & quotas · **Solution**
**Patterns:** Predictable Demands **Source:** KIA 14; KP "Predictable Demands" **Est:** 50 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`).
- **metrics-server installed** - confirm with `kubectl top nodes` (returns CPU/MEM, not an error). See `../00-cluster-setup/tooling.md`.

## Setup
```bash
kubectl create namespace lab-05-resources-qos
kubens lab-05-resources-qos          # or add -n lab-05-resources-qos to every command
```
**Predict (0): You haven't applied any `LimitRange` or `ResourceQuota` yet. If you deploy a pod with no** `resources:` block right now, will it be admitted? What QoS class will it get?

---

## Steps

### 1. Deploy one pod per QoS class
```bash
kubectl apply -f manifests/pod-guaranteed.yaml
kubectl apply -f manifests/pod-burstable.yaml
kubectl apply -f manifests/pod-besteffort.yaml
kubectl get pods -o wide
```
**Predict (1):** Open the three YAMLs. `qos-guaranteed` sets requests==limits; `qos-burstable` sets requests<limits; `qos-besteffort` sets nothing. *Before* running the next command, write down which QoS class each will get.

### 2. Read the QoS class Kubernetes derived
```bash
for p in qos-guaranteed qos-burstable qos-besteffort; do
  echo -n "$p -> "; kubectl get pod $p -o jsonpath='{.status.qosClass}'; echo
done
```
**Observe (2):** The class is **derived**, not declared - you never wrote `qosClass` anywhere. Which field combination produced each result? (Compare back to the three specs.)

### 3. Drive memory over the limit -> OOMKilled (incompressible)
```bash
kubectl apply -f manifests/pod-mem-oom.yaml
kubectl get pod mem-hog -w          # watch RESTARTS climb; Ctrl-C after ~60s
```
**Predict (3):** `mem-hog` has a 64Mi memory **limit** and `stress` tries to allocate ~150Mi. What `STATUS`/`RESTARTS` pattern will you see - will it ever stay Running?

Now read the cause from the pod, not the logs:
```bash
kubectl describe pod mem-hog | sed -n '/Last State\|Reason\|Exit Code\|Restart/p'
kubectl get pod mem-hog -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason} {.status.containerStatuses[0].lastState.terminated.exitCode}'; echo
```
**Observe (3):** What is the termination `Reason` and the exit code? (Memorize this pair - it's the OOM fingerprint.)

### 4. Drive CPU to the limit -> throttled, NOT killed (compressible)
```bash
kubectl apply -f manifests/pod-cpu-throttle.yaml
kubectl get pod cpu-hog              # note RESTARTS
sleep 20
kubectl top pod cpu-hog              # metrics-server; CPU should sit AT the limit
```
**Predict (4): `cpu-hog` runs `stress --cpu 2` (wants ~2000m) under a 200m** CPU limit. Will it be killed like `mem-hog` was? What number will `kubectl top` show for CPU?

**Prove it (4):** It's throttled, not killed - read the kernel's own throttling counters from inside the cgroup:
```bash
# cgroup v2 (most modern clusters): nr_throttled and throttled_usec keep rising
kubectl exec cpu-hog -- sh -c 'cat /sys/fs/cgroup/cpu.stat 2>/dev/null || cat /sys/fs/cgroup/cpu/cpu.stat'
sleep 10
kubectl exec cpu-hog -- sh -c 'cat /sys/fs/cgroup/cpu.stat 2>/dev/null || cat /sys/fs/cgroup/cpu/cpu.stat'
kubectl get pod cpu-hog              # RESTARTS still 0
```
Convince yourself `nr_throttled`/`throttled_usec` (cgroup v2) or `nr_throttled`/`throttled_time` (v1) **grow** between the two reads while `RESTARTS` stays `0`. (If your platform blocks reading the cgroup file, the `kubectl top` cap at ~200m plus 0 restarts is enough proof.)

### 5. (No-op for QoS, on purpose) confirm BestEffort still alive
```bash
kubectl get pod qos-besteffort -o jsonpath='{.status.qosClass}'; echo
```
**Observe (5): Still `BestEffort`. Note it now - in Break it** it's the first to die under pressure.

### 6. Apply a LimitRange -> a pod with NO requests gets DEFAULTED
```bash
kubectl apply -f manifests/limitrange.yaml
kubectl describe limitrange lab-05-defaults
kubectl apply -f manifests/pod-no-requests.yaml      # this pod declares NO resources
kubectl get pod no-requests -o jsonpath='{.spec.containers[0].resources}'; echo
kubectl get pod no-requests -o jsonpath='{.status.qosClass}'; echo
```
**Predict (6):** `pod-no-requests.yaml` has no `resources:` block at all. After the LimitRange exists, what requests/limits will the running pod actually have, and what QoS class?

**Prove it (6):** The `resources` jsonpath shows **injected** `requests` (`50m`/`64Mi`) and `limits` (`200m`/`128Mi`) that you never wrote. Defaulting happened at admission.

### 7. Apply a ResourceQuota -> requests become MANDATORY, and aggregate caps bite
```bash
kubectl apply -f manifests/resourcequota.yaml
kubectl get resourcequota lab-05-quota -o wide        # USED vs HARD
```
**Predict (7a): With the quota in place, delete and re-create the explicit BestEffort pod (no requests, and assume the LimitRange is not** defaulting it - see note). What happens at admission?
```bash
kubectl delete pod qos-besteffort --ignore-not-found
# Try to recreate a truly request-less pod by bypassing defaults is hard once a
# LimitRange exists, so instead prove the OTHER quota rule: the aggregate cap.
```
**Predict (7b):** `pod-quota-buster.yaml` is well-formed (it *has* requests) but asks for **800Mi of memory requests; the quota caps `requests.memory` at 512Mi** namespace-wide. Apply it:
```bash
kubectl apply -f manifests/pod-quota-buster.yaml      # expect this to FAIL
```
**Observe (7):** Read the exact rejection message. Which resource did it exceed, and what were the USED / LIMITED / REQUESTED numbers in the error?

**Prove it (7):** Show the quota *would* reject a request-less pod too. Temporarily remove the defaulter and try a bare pod:
```bash
kubectl delete limitrange lab-05-defaults
kubectl delete pod no-requests --ignore-not-found
kubectl apply -f manifests/pod-no-requests.yaml       # expect FAIL: must specify requests
kubectl apply -f manifests/limitrange.yaml            # put the defaulter back
```
The error names the missing `requests.cpu`/`requests.memory` - proof that once a ResourceQuota exists, every container must declare requests (directly or via a LimitRange default).

---

## Verify
```bash
# QoS classes derived correctly:
kubectl get pod qos-guaranteed -o jsonpath='{.status.qosClass}'; echo   # -> Guaranteed
kubectl get pod qos-burstable  -o jsonpath='{.status.qosClass}'; echo   # -> Burstable

# Memory pod was OOMKilled with exit 137:
kubectl get pod mem-hog -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason} {.status.containerStatuses[0].lastState.terminated.exitCode}'; echo
#   -> OOMKilled 137

# CPU pod throttled but alive:
kubectl get pod cpu-hog -o jsonpath='RESTARTS={.status.containerStatuses[0].restartCount}'; echo   # -> RESTARTS=0

# LimitRange defaulted a request-less pod:
kubectl get pod no-requests -o jsonpath='{.spec.containers[0].resources.requests}'; echo            # -> non-empty

# Quota rejection is visible (re-run; it should print "exceeded quota"):
kubectl apply -f manifests/pod-quota-buster.yaml 2>&1 | grep -i quota
```
yes Success = Guaranteed/Burstable classes correct, `mem-hog` shows `OOMKilled`/`137`, `cpu-hog` has 0 restarts, `no-requests` carries injected requests, and the quota-buster apply prints an "exceeded quota" error.

---

## Break it - eviction order + the OOM loop

### B1 - Memory limit below the working set = instant OOM loop
You already saw it: `mem-hog` never stabilizes. Confirm the loop is *the limit*, not the app, by giving it room:
```bash
kubectl get pod mem-hog -o jsonpath='RESTARTS={.status.containerStatuses[0].restartCount}'; echo
# Edit the limit up to 256Mi (above the 150Mi working set) and watch it settle:
kubectl patch pod mem-hog --type=json \
  -p='[{"op":"replace","path":"/spec/containers/0/resources/limits/memory","value":"256Mi"}]' 2>&1 || \
  echo "(pods are largely immutable; instead edit manifests/pod-mem-oom.yaml limit->256Mi, delete & re-apply)"
```
**Predict (B1):** With a 256Mi limit and a ~150Mi working set, does the OOM loop stop? (If the patch is rejected because the field is immutable, set the limit to `256Mi` in the manifest, `kubectl delete pod mem-hog`, re-apply, and observe.)

### B2 - Under node memory pressure, BestEffort dies first
**Do NOT crash your node.** Keep numbers small. The reliable, *safe* way to see eviction order is to read the kubelet's own ranking and events rather than actually starving a node:
```bash
# Re-create the BestEffort pod if you deleted it (delete the LimitRange first so it stays request-less),
# or just reason from the oom_score_adj the kubelet assigned:
kubectl get pod qos-guaranteed qos-burstable -o jsonpath='{range .items[*]}{.metadata.name}{" qos="}{.status.qosClass}{"\n"}{end}'
# Inspect the OOM score the kubelet gave each class (higher = killed sooner):
for p in qos-guaranteed qos-burstable; do
  echo -n "$p oom_score_adj: "
  kubectl exec $p -- sh -c 'cat /proc/1/oom_score_adj' 2>/dev/null || echo "(n/a)"
done
```
**Predict (B2):** Rank the three classes by `oom_score_adj` (and therefore eviction/OOM order). Which is killed *first* when the node runs low on memory, which is protected *last*?

**Observe (B2):** If you ever see a real eviction in a busy cluster, it shows up here:
```bash
kubectl get events --sort-by=.lastTimestamp | grep -iE 'evict|oom'
```
The kubelet evicts in order **BestEffort -> Burstable -> Guaranteed**. (Eviction thresholds are cloud-agnostic; what differs per cloud is node *allocatable* - see EKS/OVH below.)

### EKS
- Node **allocatable** (schedulable memory after kube/system reserved + eviction threshold) depends on instance type. A `t3.large` (8 GiB) reserves a chunk for the kubelet/OS, so don't size requests to the raw instance memory.
- Default hard eviction signal is `memory.available<100Mi` (kubelet default); the *order* across QoS is identical to any cluster.

### OVH
- Same QoS/eviction semantics. Node allocatable varies by **flavor** (e.g. `b2-7` ≈ 7 GiB raw, less allocatable). Verify with `kubectl describe node <n> | sed -n '/Allocatable/,/System Info/p'`.
- Eviction thresholds are cloud-agnostic kubelet behavior; only the absolute numbers shift with the flavor.

---

## Cleanup
```bash
kubectl delete namespace lab-05-resources-qos
```
No cloud LB/volume was created in this lab - deleting the namespace removes all pods, the LimitRange and the ResourceQuota.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
