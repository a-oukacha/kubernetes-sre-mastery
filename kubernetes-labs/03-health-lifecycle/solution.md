# Lab 03 - Health probes & graceful lifecycle · **Solution**
**Patterns:** Health Probe + Managed Lifecycle **Source: KIA 4,5,17; KP "Health Probe"/"Managed Lifecycle"; DDS health Est:** 60 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

## Objective
Tell the three probe types apart by *watching what each one does*: **liveness** restarts a wedged container, **readiness pulls a pod out of the Service while leaving it Running, startup holds the other two off while a slow app boots. Then wire `preStop` + `terminationGracePeriodSeconds` and prove you can roll the whole Deployment under load with zero dropped requests**.

## Concepts exercised
- `livenessProbe` / `readinessProbe` / `startupProbe` (exec, httpGet, tcp, gRPC)
- `initialDelaySeconds`, `periodSeconds`, `failureThreshold`, `successThreshold`
- the three probes are **independent** and do different things on failure
- EndpointSlice membership is gated by **readiness**, not by liveness or pod phase
- `RESTARTS` (restart-in-place) vs reschedule
- `preStop` hook, `terminationGracePeriodSeconds`, the SIGTERM -> (grace) -> SIGKILL contract
- the endpoint-removal-vs-SIGTERM **race** and why `preStop` sleep closes it

## Prerequisites
- Labs 01-02 done (you know `apply`, labels/selectors, `describe`+events, ReplicaSets, and `kubectl rollout`).
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`).
- No cloud add-ons - everything here is ClusterIP + in-cluster fortio.

## Setup
```bash
kubectl create namespace lab-03-health-lifecycle
kubens lab-03-health-lifecycle      # or add -n lab-03-health-lifecycle to every command
```
**Predict (0):** Open `manifests/app-probed.yaml`. All three probes are `exec` probes that read sentinel files (`/tmp/started`, `/tmp/ready`, `/tmp/healthy`). The container's command sleeps `STARTUP_DELAY=20` seconds *before* it creates those files and starts serving. While it's sleeping, which of the three probes is the only one the kubelet is actually running?

---

## Steps

### 1. Deploy the slow-starting app and watch the startupProbe gate the rest
```bash
kubectl apply -f manifests/app-probed.yaml
kubectl get pods -l app.kubernetes.io/name=health-demo -w   # watch ~30s, Ctrl-C after all are 1/1
```
**Observe (1): For the first ~20 s the `READY` column shows `0/1` and `RESTARTS` stays `0`. The pods are not** ready, but they are **not** being killed either. Note the moment `READY` flips to `1/1`. (Look at one pod's conditions: `kubectl get pod <name> -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}'`.)

**Prove it (2):** The startupProbe is what protects the slow boot. Read the probe wiring straight off the running pod:
```bash
kubectl get pod -l app.kubernetes.io/name=health-demo -o jsonpath='{.items[0].spec.containers[0].startupProbe}{"\n"}'
kubectl describe pod -l app.kubernetes.io/name=health-demo | sed -n '/Events:/,$p'
```
You should see the startup gate (`failureThreshold 30 × period 2s` = 60 s budget) and, in events, that `Started` happened but no `Unhealthy`/`Killing` during boot.

### 2. Send real traffic through the Service
```bash
kubectl apply -f manifests/loadgen.yaml
kubectl logs loadgen -f      # leave this running in a second terminal; Ctrl-C to detach
```
**Observe (3):** fortio prints a rolling tally. Once the pods are ready, the error count holds steady (`Code 200` only). Keep this terminal visible for the rest of the lab - it's your "are we dropping requests?" meter.

### 3. Flip READINESS to false - pod leaves the Service but stays Running
Pick one pod and delete its readiness sentinel (the probe execs `cat /tmp/ready`):
```bash
POD=$(kubectl get pod -l app.kubernetes.io/name=health-demo -o jsonpath='{.items[0].metadata.name}')
echo "target: $POD"
kubectl exec "$POD" -- rm -f /tmp/ready
```
**Predict (4):** Within a few seconds the readiness probe fails twice (`failureThreshold: 2`, `period 2s`). What happens to this pod's `READY` column, its `STATUS`, its `RESTARTS`, and its membership in the Service's EndpointSlice?

**Observe (5):** Watch all three facts at once:
```bash
kubectl get pod "$POD" -o wide                                   # READY 0/1, STATUS still Running, RESTARTS 0
kubectl get endpointslices -l kubernetes.io/service-name=health-demo -o jsonpath='{range .items[*].endpoints[*]}{.targetRef.name}{" ready="}{.conditions.ready}{"\n"}{end}'
```
The chosen pod now shows `ready=false` (or is dropped from the addresses list) while the other two stay `ready=true`. Glance at the fortio log - error count is still flat (traffic just routes to the ready pods).

**Prove it (6):** Restore readiness and watch it rejoin:
```bash
kubectl exec "$POD" -- touch /tmp/ready
kubectl get endpointslices -l kubernetes.io/service-name=health-demo -o jsonpath='{range .items[*].endpoints[*]}{.targetRef.name}{" ready="}{.conditions.ready}{"\n"}{end}'
```
`$POD` is back to `ready=true`. You changed Service membership without restarting or rescheduling anything - readiness is a pure traffic gate.

### 4. Cause LIVENESS to fail - kubelet restarts the container in place
Same pod, different sentinel (the liveness probe execs `cat /tmp/healthy`):
```bash
kubectl get pod "$POD" -o jsonpath='{.status.containerStatuses[0].restartCount}{"\n"}'   # baseline, expect 0
kubectl exec "$POD" -- rm -f /tmp/healthy
kubectl get pod "$POD" -w        # watch ~30s; Ctrl-C once RESTARTS increments and it returns to 1/1
```
**Predict (7): Liveness fails `failureThreshold: 3 × period 5s`. When it trips: does the pod get a new name** (rescheduled) or keep its name with `RESTARTS` going up (restarted in place)? Will it come back ready on its own?

**Observe (8):** Confirm the restart and that the pod object is the *same* one:
```bash
kubectl get pod "$POD" -o jsonpath='{.status.containerStatuses[0].restartCount}{"\n"}'   # now 1
kubectl describe pod "$POD" | sed -n '/Events:/,$p' | grep -E 'Unhealthy|Killing|Started'
```
`RESTARTS` is now `1`, the name is unchanged, and the container re-ran its boot (the wrapper re-creates all three sentinels), so it self-heals back to `1/1`. (The same `rm /tmp/healthy` on a container whose liveness check hit a *real* downstream dependency would restart it for a dependency blip - remember that for the lecture.)

### 5. Graceful shutdown: roll the Deployment under load, watch for zero 5xx
The reference manifest already has `preStop: sleep 10` and `terminationGracePeriodSeconds: 30`. Force a rollout while fortio hammers the Service, and read the error count before vs after.
```bash
# note the current total in the loadgen log, then trigger a no-op-ish rollout:
kubectl rollout restart deployment/health-demo
kubectl rollout status deployment/health-demo --timeout=180s
```
**Observe (9):** During the roll, watch a terminating pod actually linger:
```bash
kubectl get pods -l app.kubernetes.io/name=health-demo        # you'll catch one in STATUS=Terminating
```
**Prove it (10): When `rollout status` reports success, check the fortio tally in the `loadgen` log. The `Code 200` count kept climbing and non-200 / connection errors stayed at 0 across the whole rollout. Zero-downtime rollout = readiness gating new pods (lab 02) +** `preStop`/grace draining old ones.

---

## Verify
```bash
# 1) fortio saw zero errors through the rollout (read the loadgen log tail):
kubectl logs loadgen | grep -E 'Code 200|Code [45]|Sockets|Error' | tail -n 8
# 2) a NotReady pod is dropped from the endpoint set (re-run after an `rm /tmp/ready`):
kubectl get endpointslices -l kubernetes.io/service-name=health-demo \
  -o jsonpath='{range .items[*].endpoints[*]}{.targetRef.name}{" ready="}{.conditions.ready}{"\n"}{end}'
# 3) liveness failure incremented restartCount on the SAME pod (not a new one):
kubectl get pods -l app.kubernetes.io/name=health-demo \
  -o jsonpath='{range .items[*]}{.metadata.name}{" restarts="}{.status.containerStatuses[0].restartCount}{"\n"}{end}'
```
yes Success = fortio shows only `Code 200` (no 5xx/connection errors) across the rollout; a pod with readiness off is `ready=false` in the EndpointSlice while still `Running`; and the liveness-failed pod kept its name with `restarts>=1`.

---

## Break it - too-aggressive liveness on a slow starter -> CrashLoopBackOff
The `health-crash` Deployment is the *same* 20-second slow boot, but with an HTTP `livenessProbe` that starts at 1 s, fails after 3 checks (~6 s), and **no startupProbe** to suppress it during boot.
```bash
kubectl apply -f manifests/app-aggressive.yaml
kubectl get pod -l app.kubernetes.io/name=health-crash -w     # watch ~90s; Ctrl-C when you've seen the loop
```
**Predict (B1):** The app needs 20 s before it listens on :8080. The liveness probe gives it ~6 s. What `STATUS` will the pod cycle into, and will `RESTARTS` keep climbing or settle?

**Observe (B2):** Read the story from events, not logs (the process never finishes booting):
```bash
kubectl describe pod -l app.kubernetes.io/name=health-crash | sed -n '/Events:/,$p'
kubectl get pod -l app.kubernetes.io/name=health-crash \
  -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}{"\n"}'
```
You'll see repeating `Unhealthy` -> `Killing` -> `Started` events and a climbing restart count, with `STATUS` settling on `CrashLoopBackOff` (the kubelet backs off between restarts).

Prove it (B3) - fix it with a startupProbe. Add the startup gate so liveness is disabled until boot completes:
```bash
kubectl patch deployment health-crash --type=json -p='[
  {"op":"add","path":"/spec/template/spec/containers/0/startupProbe",
   "value":{"exec":{"command":["/bin/sh","-c","wget -qO- http://localhost:8080/healthz"]},
            "periodSeconds":2,"failureThreshold":30}}]'
kubectl rollout status deployment/health-crash --timeout=120s
kubectl get pod -l app.kubernetes.io/name=health-crash
```
The new pod boots for its full 20 s under the startupProbe's 60 s budget, then goes `1/1` and **stops thrashing** - the startupProbe converts "kill the slow app" into "wait for the slow app."

---

## Cleanup
```bash
kubectl delete namespace lab-03-health-lifecycle
```
No cloud LB/volume was created in this lab - deleting the namespace is enough.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
