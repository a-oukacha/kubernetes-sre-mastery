# Lab 03 - Health probes & graceful lifecycle · **Exercise**
**Patterns:** Health Probe + Managed Lifecycle **Source: KIA 4,5,17; KP "Health Probe"/"Managed Lifecycle"; DDS health Est:** 60 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

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
- A reachable cluster (2-3 `Ready` nodes).
- No cloud add-ons - everything here is ClusterIP + in-cluster fortio.

## Setup
Create a namespace **`lab-03-health-lifecycle`** and make it the default for the rest of this lab (so you don't have to pass `-n` every time).

**Predict (0):** Open `manifests/app-probed.yaml`. All three probes are `exec` probes that read sentinel files (`/tmp/started`, `/tmp/ready`, `/tmp/healthy`). The container's command sleeps `STARTUP_DELAY=20` seconds *before* it creates those files and starts serving. While it's sleeping, which of the three probes is the only one the kubelet is actually running?

---

## Tasks

### 1. Deploy the slow-starting app and watch the startupProbe gate the rest
Apply `manifests/app-probed.yaml` and watch the `health-demo` pods come up - don't just snapshot once, watch the `READY` and `RESTARTS` columns change over the first ~30 seconds until every pod reaches `1/1`.

**Observe (1):** During the slow boot, what do the `READY` and `RESTARTS` columns read, and what does that combination tell you about whether the pods are being killed? Note the moment `READY` flips to `1/1`. Inspect one pod's `Ready` condition under `status.conditions` to confirm.

**Prove it (2):** The startupProbe is what protects the slow boot. Read the `startupProbe` wiring straight off a running pod's container spec, and pull the `Events:` block from `describe`. Work out the probe's total boot budget (its `failureThreshold` × `periodSeconds`), and confirm from the events that `Started` fired without any `Unhealthy`/`Killing` during boot.

### 2. Send real traffic through the Service
Apply `manifests/loadgen.yaml` and follow the `loadgen` pod's logs in a second terminal, leaving them running.

**Observe (3):** fortio prints a rolling tally. Once the pods are ready, what should the error count do, and which response codes should you see? Keep this terminal visible for the rest of the lab - it's your "are we dropping requests?" meter.

### 3. Flip READINESS to false - pod leaves the Service but stays Running
Pick one `health-demo` pod and make its readiness probe fail by removing the readiness sentinel file (`/tmp/ready`) inside the container.

**Predict (4):** Within a few seconds the readiness probe fails twice (`failureThreshold: 2`, `period 2s`). What happens to this pod's `READY` column, its `STATUS`, its `RESTARTS`, and its membership in the Service's EndpointSlice?

**Observe (5):** Watch all three facts at once: the pod's `READY`/`STATUS`/`RESTARTS`, and the `ready` condition of each endpoint in the Service's EndpointSlice (selectable by `kubernetes.io/service-name=health-demo`). Compare the chosen pod against the other two, and glance at the fortio log to see whether the error count moved.

**Prove it (6):** Restore readiness by re-creating the `/tmp/ready` sentinel, then re-check the EndpointSlice and confirm the pod rejoins as ready. Convince yourself you changed Service membership without restarting or rescheduling anything - readiness is a pure traffic gate.

### 4. Cause LIVENESS to fail - kubelet restarts the container in place
On the same pod, record the current `restartCount` as a baseline, then make the liveness probe fail by removing the liveness sentinel file (`/tmp/healthy`). Watch the pod for ~30 seconds until `RESTARTS` increments and it returns to `1/1`.

**Predict (7): Liveness fails `failureThreshold: 3 × period 5s`. When it trips: does the pod get a new name** (rescheduled) or keep its name with `RESTARTS` going up (restarted in place)? Will it come back ready on its own?

**Observe (8):** Confirm the new `restartCount`, that the pod's name is unchanged, and read the `Unhealthy` / `Killing` / `Started` events from `describe`. Decide whether this was a restart-in-place or a reschedule, and explain why the pod self-heals back to `1/1`.

### 5. Graceful shutdown: roll the Deployment under load, watch for zero 5xx
The reference manifest already has `preStop: sleep 10` and `terminationGracePeriodSeconds: 30`. Note the current request total in the loadgen log, then trigger a rollout restart of the `health-demo` Deployment and wait for the rollout to report success.

**Observe (9):** While the roll is in progress, list the pods and try to catch one in `STATUS=Terminating` - confirm a terminating pod actually lingers rather than disappearing instantly.

**Prove it (10): When the rollout reports success, check the fortio tally in the `loadgen` log. Did the `Code 200` count keep climbing while non-200 / connection errors stayed at 0 across the whole rollout? Tie the result back to its two causes: readiness gating new pods (lab 02) plus** `preStop`/grace draining old ones.

---

## Verify
Demonstrate success with observable signals:
- the loadgen log shows fortio saw only `Code 200` (no 4xx/5xx/connection errors) across the rollout;
- a pod whose readiness is off reads `ready=false` in the EndpointSlice while its `STATUS` is still `Running`;
- the liveness-failed pod kept its name with a `restartCount` of at least 1.

yes Success = fortio shows only `Code 200` (no 5xx/connection errors) across the rollout; a pod with readiness off is `ready=false` in the EndpointSlice while still `Running`; and the liveness-failed pod kept its name with `restarts>=1`.

---

## Break it - too-aggressive liveness on a slow starter -> CrashLoopBackOff
The `health-crash` Deployment is the *same* 20-second slow boot, but with an HTTP `livenessProbe` that starts at 1 s, fails after 3 checks (~6 s), and **no startupProbe** to suppress it during boot. Apply `manifests/app-aggressive.yaml` and watch the `health-crash` pod for ~90 seconds until you've seen the loop.

**Predict (B1):** The app needs 20 s before it listens on :8080. The liveness probe gives it ~6 s. What `STATUS` will the pod cycle into, and will `RESTARTS` keep climbing or settle?

**Observe (B2):** Read the story from events, not logs (the process never finishes booting). Pull the `Events:` block and the container's `restartCount`. Which event `Reason`s repeat, in what order, and what `STATUS` does the pod settle on?

Prove it (B3) - fix it with a startupProbe. Patch the `health-crash` Deployment to add a `startupProbe` on the container (an HTTP-style check against `/healthz` on :8080, with enough `failureThreshold × periodSeconds` budget to cover the 20 s boot), then wait for the rollout. Confirm the new pod boots for its full ~20 s under the startup gate, goes `1/1`, and stops thrashing - the startupProbe converts "kill the slow app" into "wait for the slow app."

---

## Cleanup
Delete the `lab-03-health-lifecycle` namespace. No cloud LB/volume was created here, so that's enough.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
