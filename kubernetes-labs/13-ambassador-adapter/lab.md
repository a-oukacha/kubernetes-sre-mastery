# Lab 13 - Ambassador & Adapter · **Exercise**
**Patterns:** Ambassador + Adapter **Source: KP "Ambassador"/"Adapter"; DDS ch4/5 Est:** 50 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations and the proofs yourself; that *is* the skill. Attempt every task and write down
> your answer to every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done,
> [`solution.md`](solution.md) has the exact commands + the output you should have seen + every
> checkpoint answer. Then read [`lecture.md`](lecture.md) for the course.

## Objective
Decouple an app from network topology with an **Ambassador sidecar (the app talks only to `localhost`; the ambassador handles where the real backend lives), then normalize the app's operational interface with an Adapter sidecar (a redis that only speaks its native `INFO` gets a standard Prometheus `/metrics` endpoint). Reroute the backend with zero app change**, and produce uniform telemetry without touching the app.

## Concepts exercised
- **Ambassador** = an outbound proxy sidecar. App connects to `localhost:<port>`; the ambassador owns discovery/routing/retries/TLS to the real backend.
- **Adapter** = a translation sidecar. It reads the app's native stats and exposes them in a STANDARD interface (Prometheus `/metrics`).
- Shared pod network namespace (`localhost` between containers); shared lifecycle.
- ConfigMap-driven proxy target; `kubectl apply` overwrite + pod restart as the reroute mechanism.
- Prometheus exposition format (`# HELP` / `# TYPE` / metric lines); optional ServiceMonitor scrape.

## Prerequisites
- Labs **01** (kubectl, pods, labels), **06** (Services & DNS), **12** (init & sidecars - if you skipped it, just know that containers in one pod share network + lifecycle).
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`). See `../00-cluster-setup/`.
- Optional: `kube-prometheus-stack` from `../00-cluster-setup/` §2.8 for the last (optional) scrape step.

## Setup
Create the namespace **`lab-13-ambassador-adapter`** and make it your default. Then bring up the two real backends and the ambassador's first config by applying `manifests/backend-a.yaml`, `manifests/backend-b.yaml`, and `manifests/ambassador-config-a.yaml`. List the pods, services, and configmaps to confirm they landed.

**Predict (0):** Open `manifests/pod-ambassador.yaml`. The `app` container connects to which address? Does that string mention `backend-a`, `backend-b`, or any Service name at all?

---

## Tasks

### 1. Start the app + ambassador; prove writes land in backend-A
Apply `manifests/pod-ambassador.yaml` and wait for `app-with-ambassador` to be Ready. The app writes a key every 2s to `localhost:6379`; confirm from its own logs that it believes it is writing to `localhost`. Then inspect both backends to discover where the keys actually land.

**Observe (1):** One backend's key count grows while the other stays empty. Which one received the writes, and what does that tell you about what the ambassador did with the app's `localhost` traffic?

### 2. Confirm the app truly only knows `localhost`
Without trusting the logs, prove from the live pod spec that the `app` container's only configured network target is `127.0.0.1`/`localhost` - no Service name, no `backend-a`, anywhere in its args. Then, from inside the `app` container, reach the backend through the loopback address and confirm it answers.

**Observe (2):** The only network address visible to the app is loopback. There is no backend hostname anywhere in the app spec - the topology lives entirely in the sidecar.

### 3. Reroute to backend-B by changing ONLY the ambassador config
Without editing a single byte of `pod-ambassador.yaml`, redirect traffic to the other backend. Apply `manifests/ambassador-config-b.yaml` (same ConfigMap name, one line differs), then restart the pod so the ambassador re-reads its config at start, and re-apply the **same unchanged** app manifest. Wait for Ready.

**Predict (3):** After the restart, which backend's key count will now climb - A or B? Did you change a single byte of the app container?

### 4. Prove the reroute happened with zero app change
Give the app a few seconds to write, then inspect both backends again: show that new writes are now landing in the other backend while the first is frozen at its old count. Also re-read the app's written key through loopback to confirm the app itself is unaware anything moved.

**Prove it (4):** New writes appear in the second backend; the first is no longer growing. The app still believes it is writing "via localhost" - it never noticed the topology moved underneath it.

### 5. Adapter: give a native-only redis a standard `/metrics`
Apply `manifests/pod-adapter.yaml` and wait for `redis-with-adapter` to be Ready. First, look at the redis's NATIVE operational interface - its RESP `INFO` output, which Prometheus cannot scrape. Then query the adapter (exporter) container's STANDARD interface on port 9121 and look for Prometheus exposition lines (`# HELP` / `# TYPE` / `redis_*`).

**Observe (5):** The same redis now exposes Prometheus text translated from `INFO`, even though the redis container itself was never instrumented. What did the adapter change - the app, or only its *interface*?

### 6. (Optional) Let Prometheus scrape the normalized endpoint
Only if `kube-prometheus-stack` is installed (cluster-setup §2.8) - otherwise skip, since task 5 already proved the interface. Apply `manifests/servicemonitor.yaml` (it will fail harmlessly if the Operator CRDs are absent), then in the Prometheus UI query for `redis_up` scoped to this namespace.

**Observe (6):** A single generic scrape rule works because the adapter made the output standard - no redis-specific Prometheus configuration was needed. Why is that the whole point of the Adapter pattern?

---

## Verify
Demonstrate success with observable signals: the second backend's key count is non-zero (it now receives writes) while the first is frozen, AND the adapter's `/metrics` endpoint returns Prometheus-format output (at least one `# TYPE` line).

yes Success = writes follow the ambassador's target (now B) while the app manifest is byte-for-byte unchanged, AND `/metrics` returns Prometheus text.

---

## Break it - the ambassador is now in the request path
The ambassador decoupled the app from topology, but it also inserted itself into every request. First confirm `backend-b` is perfectly healthy on its own (it answers `PING`). Then deliberately break the ambassador's config - point its backend at a host that does not exist - by overwriting the `ambassador-config` ConfigMap, restart the pod, and read the app's logs after it has tried to write.

**Predict (B1):** `backend-b` answers on its own. Will the app's writes succeed now? Where does the failure surface - in the backend, or in the ambassador hop?

**Observe (B2):** The app's traffic to `localhost:6379` now fails or hangs even though backend-b is healthy. The break is entirely in the ambassador hop - proof that you traded topology-coupling for a dependency on the sidecar (a latency point and a SPOF you must monitor).

**Restore:** Re-apply the good `manifests/ambassador-config-b.yaml`, restart the pod, and confirm the second backend's key count is climbing again.

### EKS
No cloud-specific steps - everything here is pod-internal (shared netns, ConfigMap, sidecar). No LoadBalancer, no EBS. The optional ServiceMonitor needs the Prometheus Operator (kube-prometheus-stack) just as on any cluster. Describe what (if anything) you would change on EKS, and confirm it is nothing.

### OVH
No cloud-specific steps - identical to EKS. Pod-internal only. Confirm there is nothing cloud-specific to do.

---

## Cleanup
Delete the `lab-13-ambassador-adapter` namespace. No cloud LB/volume was created in this lab, so that's enough. (If you applied the ServiceMonitor, it lives in the namespace and is deleted with it.)

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
