# Lecture 02 - Declarative Deployment: ReplicaSets, rolling updates & the rollout-stall safety net

## Answers to the lab checkpoints
- **(1)** **One** Deployment, **one** ReplicaSet, **three** Pods. The Deployment doesn't manage pods directly - it manages a ReplicaSet, and the ReplicaSet manages the pods. `describe pod` shows `Controlled By: ReplicaSet/podinfo-<hash>`, and `describe rs` shows `Controlled By: Deployment/podinfo`. Two layers of ownership, each a controller closing the gap to its desired count.
- **(2) The `pod-template-hash` is a hash of the pod template** (the `spec.template` block). The Deployment computes it, names the ReplicaSet `podinfo-<hash>`, and injects the label into both the RS selector and every pod it stamps out. That's how a Deployment can own several ReplicaSets at once without their selectors colliding - each generation of the template gets a distinct hash, hence a distinct RS. Change the template (new image, new args) -> new hash -> new ReplicaSet.
- **(3) fortio is making real HTTP calls through the `ClusterIP` Service, which load-balances across whatever pods are currently in its endpoints**. The 200s prove the Service is routing to Ready pods. This loop is your *measurement instrument* for the rest of the lab.
- **(4)** Max total pods = **4 (3 desired + `maxSurge: 1`). Min Ready pods = 3** (`maxUnavailable: 0` forbids dropping below `replicas − 0`). So the pattern is: add one new pod, wait for it to be Ready, *then* retire one old pod - never dipping below full capacity. That's exactly why this config is safe under load.
- **(5)** The `Code 200` count climbs straight through the rollout. You may see a tiny number of `-1`/non-200s from connections that were mid-flight when an old pod was removed - that's a graceful-shutdown / connection-draining concern (lab 03), not a rollout-strategy failure. The headline: a `maxUnavailable: 0` rolling update over Ready-gated pods is effectively zero-downtime.
- **(6) The old ReplicaSet is scaled to `0` but retained** as the **rollback target**. `rollout undo` works by scaling the previous RS back up and the current one down - no image re-pull of a "previous" tag, no guesswork. `revisionHistoryLimit: 5` caps how many of these zeroed RSes are kept; older ones are garbage-collected.
- **(7)** `rollout status` **never prints success - it blocks (until your `--timeout`). The Deployment creates the v3 RS and scales it to 1 pod (the `maxSurge`), but that pod never becomes Ready (its readiness probe 404s), so `maxUnavailable: 0` forbids removing any v2 pod. The rollout is wedged at one new pod, 0 Ready, and the Service keeps serving the 3 healthy v2 pods. The bad version takes no** production traffic.
- **(B1)** **No. A pod that is `Running` but `0/1 READY` is not in the Service's endpoints. The endpoints/EndpointSlice controller only adds a pod's IP once its readiness gate passes. So the v3 pod exists, burns a little CPU, and receives zero** Service traffic. Readiness is the single mechanism doing the protecting.
- **(B2)** The Deployment shows `Progressing` going to `reason: ProgressDeadlineExceeded` (after `progressDeadlineSeconds`, default 600s) - but *before* that it sits in `Progressing=True, reason: ReplicaSetUpdated` / "Waiting for rollout to finish: 0 of 3 updated replicas are available." The load loop shows no errors because the new pods never entered the endpoints - the live fleet never changed. A stalled rollout is a *non-event* for users, which is the whole point.
- **(B3) After `undo`, podinfo is `3/3` on v2 and the v3 RS is back to `0`. `rollout history` shows a new revision (the undo is recorded as a forward revision pointing at the old template). The broken v3 served zero** requests to the load loop - confirm the `Code 200` count is uninterrupted and non-200s stayed ~0 across the entire episode. You shipped a bad release and nobody noticed. That's the safety net working.

---

## What just happened (under the hood)
A Deployment is a controller that manages **ReplicaSets, and each ReplicaSet is a controller that manages Pods**. Three reconcile loops stacked:

1. **You change the pod template (new image/args via `apply` or `set image`). The Deployment controller hashes the new template, sees no ReplicaSet with that `pod-template-hash`, and creates a new RS** (desired replicas governed by the surge math).
2. The Deployment ratchets the two RSes according to `strategy.RollingUpdate`. With `maxSurge: 1, maxUnavailable: 0` it scales the new RS up by 1, waits for that pod to be **Ready** *and* to have been ready for `minReadySeconds`, then scales the old RS down by 1. Repeat until the new RS holds all replicas and the old holds none.
3. **Readiness is the gate.** A pod only counts as "available" - and only enters the Service's endpoints - once its `readinessProbe` passes (and `minReadySeconds` elapses). The Deployment's progress is defined in terms of *available* replicas, not *running* ones. This is the load-bearing detail.

Now the safety property falls out for free. The broken v3 pod is `Running` (its process is alive, liveness passes) but **never Ready** (readiness 404s). So:
- it is **never added to endpoints** -> it takes no traffic;
- it **never counts as available** -> with `maxUnavailable: 0` the Deployment refuses to retire a single healthy v2 pod;
- the rollout **stalls at one surged pod** and eventually trips `ProgressDeadlineExceeded`, but the live service is untouched the whole time.

A bad release becomes a **non-event** instead of an outage - *provided your readiness probe actually tests whether the pod can serve.* `rollout undo` then just reverses the ratchet: scale the retained previous RS back up, scale the wedged one down. Because the old RS still exists (that's what `revisionHistoryLimit` keeps), rollback is deterministic and fast - no image rebuild, no "what was the previous tag again."

**RollingUpdate vs Recreate.** `Recreate` kills all old pods, *then* starts new ones - guaranteeing a gap with zero capacity (and never running two versions at once). `RollingUpdate` overlaps the two versions to preserve capacity. Overlap is the price of zero-downtime, and it imposes a contract on your app (next section).

## Dev notes
- Immutable, unique image tags per build. `set image ...:6.7.1` triggers a new RS because the template hash changed. `:latest` does **not** reliably change the template, so it neither triggers a clean rollout nor gives you a deterministic rollback target - `undo` would roll back to "latest" which may have moved. Pin a real version (or a digest) on every build.
- Backward/forward compatibility is mandatory, because both versions run at once. During a RollingUpdate, v1 and v2 pods serve simultaneously behind one Service. If v2 makes a breaking DB schema or API change, v1 (still live) breaks - or vice versa. Use expand/contract (parallel-change) migrations: add the new column/field, deploy code that writes both, then remove the old - across *separate* releases.
- Readiness must mean "I can actually serve a real request," not "my process booted." A readiness probe that always returns 200 (or no probe at all) defeats every protection in this lab. Probe a path that exercises the real serving path.

## DevOps / Platform notes
- `kubectl apply`/`set image` is for learning; GitOps is the production end state. With **Argo CD** or **Flux, the Deployment manifest in Git is the source of truth; the controller continuously reconciles the cluster to match and flags drift** (someone's `kubectl edit` gets reverted or alarmed). You stop running `kubectl apply` by hand entirely.
- Native Deployments do rolling/recreate only. For **canary** (1% -> 10% -> 100%) or **blue-green with automated analysis, reach for Argo Rollouts** or **Flagger**: they watch metrics (success rate, latency) during the rollout and auto-promote or auto-abort. The native Deployment's "stall on unready" is a blunt version of the same idea - it aborts on *readiness*, they abort on *SLOs*.
- **`revisionHistoryLimit`** is a real knob: too high wastes etcd with zeroed RSes; too low (e.g. `0`) means you can't `undo`. Keep a handful.

## Architect notes (trade-offs)
Choose the strategy by **blast radius** and **capacity cost**:
- **Recreate** - full downtime window, but only one version ever runs. Use for apps that *cannot* tolerate two versions concurrently (incompatible schema, exclusive locks). Smallest correctness risk, largest availability cost.
- **RollingUpdate** - zero-downtime, but two versions overlap and a bad version partially deploys before readiness stops it. Cheapest, default, requires version compatibility.
- **Blue-green - stand up the full new fleet, flip the Service selector atomically. Instant rollback (flip back), but 2× capacity** during the cutover, and the switch is all-or-nothing (no gradual exposure).
- **Canary** - expose the new version to a slice of traffic, watch metrics, widen or abort. Smallest blast radius for a *subtly* bad release (one that's Ready but wrong), but needs traffic-splitting and metric automation Kubernetes doesn't give you natively.

The native Deployment guards against the **crash/won't-be-Ready** failure. It does **not** guard against a version that is healthy-but-wrong (returns 200s with bad answers) - that's what canary + metric analysis exists for.

## SRE notes (failure modes, SLOs, toil)
- The missing-readiness-probe brownout. Without a readiness probe, every `Running` pod is immediately "available" and immediately in endpoints. A bad rollout then *succeeds* - `rollout status` says "successfully rolled out" - while the new pods serve errors. You traded a safe stall for a silent, fleet-wide brownout. A rollout that can't fail is a rollout that can't protect you. This is the single most common self-inflicted outage in this lab's territory.
- **`minReadySeconds` is bake time.** It forces a new pod to stay Ready for N seconds before it counts, catching the pod that passes one probe then immediately falls over. It also paces the rollout so an early failure stalls early, before half the fleet is replaced.
- Automate rollback on SLO/error-budget burn. The next maturity step beyond "readiness stalls the rollout" is "an error-budget burn-rate alert (lab 20) or a progressive-delivery controller *aborts and rolls back automatically*." `rollout undo` is the manual version; you want it on a trigger.
- **`progressDeadlineSeconds`** turns a silent hang into a signal: after it elapses the Deployment reports `ProgressDeadlineExceeded`, which your CD pipeline (or alerting) can treat as a failed deploy and auto-`undo`.
- **Toil:** hand-running `kubectl set image` across many services is drift-prone toil. GitOps + a templated rollout removes it.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- Readiness = "weights loaded and warmed," not "process up." A model server (vLLM, TGI, Triton, KServe) needs to pull multi-GB weights, allocate KV-cache, maybe JIT/compile graphs and run a warm-up pass *before* it can serve a fast first token. Gate readiness on that completion (often a long `startupProbe` + readiness, lab 03). Then a rolling update of a model version behaves exactly like this lab: cold replicas stay out of the load balancer until warm, so no request hits an unwarmed replica and eats a multi-second TTFT.
- **The stall-on-unready net matters *more* for models - and is *weaker*. A crashing model server fails readiness and the rollout safely stalls. But the dangerous model release is the one that's perfectly healthy and subtly worse - slightly higher hallucination rate, a regressed eval score - which passes every probe and rolls out clean. Readiness can't catch quality regressions. That's why model rollouts use canary by traffic %** between versions plus **quality SLIs** (eval scores, human-feedback rates) as the abort signal - the same canary + metric-analysis pattern from the Architect/DevOps notes, applied to model quality instead of HTTP success rate.
- Capacity during overlap is expensive. Two GPU-backed model versions running simultaneously during a RollingUpdate doubles the most expensive resource you own. Blue-green's 2× cost is brutal here; canary's small slice is usually the right trade. (No GPU is required for this lab - this is the conceptual mapping only.)

## Pitfalls
- **No readiness probe (or a fake one)** -> a broken rollout *reports success* while serving errors. The lab's entire safety property evaporates.
- **`:latest` tags** -> the template may not change (no clean rollout) and `undo` has no deterministic target. Pin versions/digests.
- **`maxUnavailable` too aggressive** (e.g. `25%` or more under load) -> you intentionally drop capacity mid-rollout; combined with a slow-starting app, latency and errors spike even on a *good* release. For latency-sensitive services prefer `maxUnavailable: 0` + `maxSurge: 1`.
- Breaking schema/API change in one release -> the overlap window means v1 and v2 must coexist; a breaking change takes down the version you *didn't* just deploy. Use expand/contract migrations.
- **Confusing liveness with readiness** -> liveness restarts a pod (crash recovery); readiness gates traffic and rollout progress. The broken v3 here had *good* liveness and *bad* readiness on purpose - that's why it stayed `Running` but `0/1` instead of crash-looping.

## Further reading
- **KP "Declarative Deployment"** - the pattern: Rolling, Fixed (Recreate), Blue-Green, and Canary releases, and when each applies.
- **KIA ch9** - Deployments end to end: how the Deployment creates and ratchets ReplicaSets, `pod-template-hash`, `maxSurge`/`maxUnavailable`, `minReadySeconds`, `rollout status/history/undo`, and `revisionHistoryLimit`.
- For the readiness-gates-endpoints mechanism in depth: **KIA ch5** (Services, readiness probes, endpoints) - picked up in lab 03 (probes & lifecycle) and lab 06 (Services & EndpointSlices).
