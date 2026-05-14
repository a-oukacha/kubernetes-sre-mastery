# Lecture 18 - Autoscaling: HPA, VPA, Cluster Autoscaler

## Answers to the lab checkpoints
- **(0)** **1.** With no load, average CPU sits well below the 50% target, so the loop's desired count rounds down to the floor - `minReplicas: 1`. The HPA never goes below `minReplicas` (or above `maxReplicas`), and at rest it parks at the floor.
- **(1)** **~100m per pod. `50%` is relative to the `200m` request, so the loop tries to hold average use at `0.50 × 200m = 100m` per pod. That is the single most important sentence in this lab: the percentage is a fraction of the request, not of a core.**
- **(2)** `<unknown>` at first because metrics-server scrapes on its own interval and the HPA needs at least one sample *after* the pods are Ready; until a sample lands there's nothing to divide. It becomes a number the moment metrics-server reports CPU for the pods and the HPA's next ~15s sync reads it.
- **(3)** As current% crosses 50%, the HPA raises `REPLICAS` upward. With `scaleUp.stabilizationWindowSeconds: 0` and a policy of "up to 4 pods / 15s", it can jump aggressively - often to several pods within the first sync or two. It is fast *up* by design; under-provisioning hurts more than over-provisioning.
- **(4)** It stabilizes where **per-pod** average lands near 50% (≈100m each). The exact count depends on how much total CPU `fortio` extracts - typically a handful of pods (e.g. 3-5) on a small node. The mechanism: same total load ÷ more pods = lower per-pod % until it crosses back under target.
- **(5)** Scale-down does **not** start until the load has stayed low for the full `scaleDown.stabilizationWindowSeconds: 120` - so ~2 minutes after you stop the load, *then* `REPLICAS` ticks down (at most 1 pod/60s by the policy) toward `minReplicas: 1`. The asymmetry - fast up, slow down - is deliberate.
- **(6) Because the VPA in `vpa.yaml` recommends on CPU**, the same resource the HPA scales on. In `Auto`/`Recreate` mode it would *rewrite the CPU request* - the very denominator the HPA's percentage is measured against. Move the request and you move the target's meaning under the HPA's feet; the two loops chase each other. Recommend-mode (`Off`) is safe because it never mutates pods. Rule: don't HPA-on-CPU and VPA-auto-on-CPU at the same time.
- **(7)** The unschedulable pods show `Pending` with a `FailedScheduling` event reading *"Insufficient cpu"* (the node has no room for another `1` CPU request). `Pending` pods are exactly the signal the Cluster Autoscaler / node-pool autoscaler watches: within ~1-4 minutes it provisions a new node (you'll see a `TriggeredScaleUp` event and a new line in `kubectl get nodes`), the Pending pods bind to it, and once the load is gone the now-underused node is reclaimed later.
- **(B1)** `TARGETS` stays `<unknown>/50%` forever and `REPLICAS` never moves. `describe` shows a condition `ScalingActive=False`, reason `FailedGetResourceMetric` / *"did not receive metrics ... missing request for cpu"*. A `Utilization` HPA divides current CPU by the request; with no request there is no denominator, so there is no percentage and no decision. Requests are mandatory for a Utilization HPA.
- **(B2)** `REPLICAS` traces a **sawtooth**: a `10%` target trips on the slightest traffic, the loop overshoots, metrics-server's lag means the HPA is always acting on stale data, it overcorrects the other way, and with no scale-down stabilization it immediately shrinks again - repeat. The original `50%` + 120s window doesn't do this because the higher target leaves headroom (small blips don't cross it) and the window forces the loop to *wait out* the lag before shrinking, so transient dips don't trigger churn.

---

## What just happened (under the hood)
There are **two independent autoscalers** in play, watching different things:

1. The HorizontalPodAutoscaler (pods -> load). The HPA controller runs inside the controller-manager on a loop (default `--horizontal-pod-autoscaler-sync-period=15s`). Each tick it:
1. reads the current metric for the target's pods from an aggregated metrics API - `metrics.k8s.io` (served by **metrics-server**) for CPU/memory, or `custom.metrics.k8s.io` / `external.metrics.k8s.io` (served by an adapter like prometheus-adapter or KEDA) for everything else;
2. computes `desiredReplicas = ceil(currentReplicas × currentMetricValue / targetMetricValue)`;
3. clamps to `[minReplicas, maxReplicas]`, applies `behavior` policies and stabilization windows, and writes the new `spec.replicas` on the Deployment;
4. the Deployment -> ReplicaSet controller (lab 02) does the actual pod creation.

Two consequences fall straight out of that formula. **It's reactive, not predictive** - there is a built-in delay (metric scrape interval + the ~15s sync + pod startup time), so the HPA always acts on the *recent past*. And CPU utilization is a fraction of the request: `currentMetricValue / targetMetricValue` is `100m / (50% × 200m)`; remove the request and the denominator vanishes (`<unknown>`, Break-it B1). The `behavior` block (scaleUp/scaleDown policies + `stabilizationWindowSeconds`) exists *because* of the lag: it lets you be aggressive up and conservative down so lagging metrics don't make the fleet flap (Break-it B2).

2. The Cluster Autoscaler (nodes -> pods). Completely separate loop. It does **not** look at CPU%; it looks for `Pending` pods that fail to schedule for lack of room. When it finds them, it simulates "would adding a node from this node group let them schedule?" and if yes, increases the cloud node group's desired size; the cloud brings up a VM, it joins as a `Ready` node, and the scheduler binds the Pending pods. For scale-*down* it finds nodes that are under-utilized *and* whose pods could move elsewhere, then **cordons and drains** them - which is where the **PDB** matters: the eviction API refuses a drain that would drop a workload below its `minAvailable`, so scale-down can't silently take you under your availability floor.

The layering is the whole point: HPA adds pods; when pods can't fit, the Cluster Autoscaler adds nodes. You almost always run both - pod autoscaling on top of node autoscaling.

**VPA** is the third, *vertical* axis: instead of changing the replica count it changes each pod's **requests/limits to match observed usage. In recommend mode it just writes a suggestion; in auto mode it evicts and recreates pods with new requests. It conflicts with an HPA on the same** resource (it moves the request the HPA's % is relative to) - the standard advice is VPA-on-memory + HPA-on-CPU, or VPA in recommend-only mode beside an HPA.

## Dev notes
- **Design for HORIZONTAL scale. The HPA can only help if any replica can serve any request - so keep the app stateless**, externalize session/state (DB, cache, object store, lab 09/10/13), and don't pin work to a specific pod. A service that can't run as N identical copies can't be HPA'd.
- Fast startup is an autoscaling feature. Scale-up isn't done when the pod is *scheduled* - it's done when the pod is *Ready*. A 90-second cold start means 90 seconds of being under-provisioned every time you scale. Trim image size, lazy-load, and set a real readiness probe (lab 02) so the HPA's new pods take traffic the instant they can.
- **Set requests honestly.** The HPA target is a percentage of *your* request. If you pad requests "to be safe," 50% means more absolute CPU than you intended and the HPA scales late. Use `kubectl top` / VPA recommendations to right-size.

## DevOps / Platform notes
- **Tuning is the job.** `targetUtilization`, `min/maxReplicas`, and the `behavior` windows are the knobs. Common defaults: target 50-70% CPU (leave burst headroom), fast `scaleUp`, `scaleDown.stabilizationWindowSeconds` 120-300s to avoid churn. Tune from real traffic shapes, not vibes.
- metrics pipeline is a hard dependency. CPU/memory HPAs need **metrics-server (`metrics.k8s.io`). Anything else needs an adapter: prometheus-adapter maps PromQL -> `custom.metrics.k8s.io` (see `hpa-custom-metric.yaml`), or KEDA** which registers `external.metrics.k8s.io` scalers for queues/streams (Kafka lag, SQS depth, Prometheus, cron). No metrics API = `<unknown>` = no scaling.
- Cluster Autoscaler vs Karpenter (AWS): CA scales pre-defined **node groups up/down by integer counts - simple, matches OVH, but you must pre-design instance types per group. Karpenter watches Pending pods and provisions right-sized nodes directly** from a pool of instance types (better bin-packing, faster, consolidation/spot-aware) - but it's AWS-specific. Pick CA for portability, Karpenter for AWS cost/latency.
- **Cost.** Aggressive scale-up + slow scale-down trades money for safety; that's usually correct for user-facing services and wrong for batch. Watch node-hours, not just pod counts.

## Architect notes (trade-offs)
- Reactive vs predictive vs scheduled. HPA is **reactive** - it always lags. If you *know* the load shape (a 9am login spike, a nightly batch), **scheduled scaling (a CronJob bumping `minReplicas`, or KEDA cron scaler) pre-warms before the spike and sidesteps the cold-start window. Predictive/ML** autoscalers go further but add operational complexity. Most teams: reactive HPA as the floor, scheduled bumps for known patterns.
- **Layer the autoscalers deliberately.** HPA (pods) sits on top of CA/Karpenter (nodes). Make sure `maxReplicas` and the node group `max` are consistent - an HPA that wants 50 pods on a node group capped at 3 nodes will just leave pods `Pending` forever (a real pitfall).
- **Scale-to-zero** (KEDA/Knative) is an architectural choice, not just a tuning knob: it requires an activator/proxy to hold the first request while a replica cold-starts, and it changes your latency SLO for the first request after idle. Worth it for bursty/idle workloads (and *enormously* worth it for idle GPUs), costly in tail latency.

## SRE notes (failure modes, SLOs, toil)
- **Scale-up latency IS an SLO risk. A cold scale-up is metric-lag + sync + pod-start + (maybe) node-provision. For minutes during a surge you are under-provisioned, and that's exactly when you're getting hammered - error budget burns fastest right when the autoscaler hasn't caught up. Mitigate: lower the target (scale earlier), keep warm headroom (`minReplicas` above 1), pre-scale on schedule, and count scale-up time against your SLO**, not as free.
- **Thundering herd on cold start.** When many new pods start at once they may all hit a cold cache / DB connection storm / dependency simultaneously. Stagger with `behavior` policies, readiness gating, and warmup.
- PDB or you'll self-inflict an outage. Without a PDB, a node drain (scale-down, upgrade) can evict every replica at once. `minAvailable` makes the eviction path respect your floor. (It does **not** protect against involuntary loss - a node crash - that's what `minReplicas`/spread/anti-affinity are for.)
- **Metric-lag oscillation (flapping).** Too-low target + no stabilization = sawtooth (you saw it in B2). Symptoms: churning pods, noisy events, cache cold-starts. Fix: realistic target + scale-down stabilization window.
- **Over-aggressive scale-down = churn.** Reclaiming a pod/node you'll need again in 90 seconds costs you a cold start each cycle. Slow, damped scale-down is usually cheaper than the churn.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- Don't autoscale a model server on CPU. A GPU-bound inference pod can be at 100% GPU and ~5% CPU - CPU% is meaningless. Scale on a metric that reflects real saturation: **queue depth / requests-in-flight**, **GPU utilization** (NVIDIA **DCGM exporter -> Prometheus -> adapter/KEDA), or tokens/sec** / batch occupancy. The mechanism is identical to `hpa-custom-metric.yaml`, just a different metric. *(This lab requests no GPU - the GPU/DCGM/queue-depth story is conceptual.)*
- KEDA on a request queue is the common pattern. Inference requests land on a queue (SQS/Kafka/Redis); KEDA's `external.metrics.k8s.io` scaler reads the backlog and drives replicas - including **down to zero**.
- Scale-to-zero matters enormously for GPUs. An idle GPU is money on fire. Scaling a model server to zero when idle is a huge cost win - but the trade-off is brutal: **cold start = model-load time, which for an LLM is pulling/loading tens of GBs of weights into VRAM - tens of seconds to minutes. So the first request after idle eats that latency. The real decision is keep-warm (pay for idle GPU) vs scale-to-zero (pay in cold-start latency)**; teams often keep `minReplicas: 1` warm per popular model and scale-to-zero only the long tail.

## Pitfalls
- **HPA without `requests`** -> `<unknown>/50%`, never scales (Break-it B1). The single most common HPA mistake.
- HPA + VPA fighting on the same resource -> the VPA moves the request the HPA measures against; both loops thrash. Split resources or keep VPA in recommend mode.
- **Target too low / no stabilization** -> flapping (Break-it B2).
- **Ignoring scale-up latency in SLOs** -> you budget as if scaling is instant; it isn't, and the gap burns error budget during surges.
- Scale-to-zero cold-start surprising users -> the first post-idle request is slow; make it a deliberate, documented choice.
- **`maxReplicas` > node-group `max` -> HPA wants more pods than the Cluster Autoscaler can ever schedule; pods sit `Pending` forever. A node group already at max can't scale** - CA logs `max node group size reached`.
- **No metrics pipeline / wrong metric** -> custom/external HPAs sit at `<unknown>` with no adapter; CPU HPAs need metrics-server.

## Cloud specifics

### EKS
- **metrics-server** is an add-on: `eksctl create addon --name metrics-server --cluster $CLUSTER` (`eks.md §2.3`). Required for the CPU HPA.
- **Cluster Autoscaler (the course default, for OVH parity) - Helm with auto-discovery on node-group tags (`eks.md §2.7`). It scales the managed node groups up/down by count. Node groups need the discovery tags and the IAM permissions to change desired capacity. CA can only scale a node group between its min and max** - at max it stops and logs it.
- **Karpenter (modern AWS alternative) - instead of fixed node groups you define a `NodePool` (allowed instance types, zones, limits) and an `EC2NodeClass`; Karpenter watches `Pending` pods and launches right-sized nodes directly (better bin-packing, faster scale-up, native spot + consolidation** that repacks workloads onto fewer nodes). No node groups to pre-size. Trade-off: AWS-only and a different mental model. Use CA when you want portability with OVH; Karpenter when you want AWS-native cost/latency.

### OVH
- **metrics-server** is usually preinstalled on MKS; if not, install via Helm (`ovh.md §2.4` style / `tooling.md`). Confirm with `kubectl top nodes`.
- **Node-pool autoscaler - OVH manages a Cluster-Autoscaler-equivalent for the pool; you set min/max on the node pool (e.g. `min=2 max=5`, `ovh.md §2.5`). Same signal as EKS CA: `Pending` pods trigger a new node; underused nodes are reclaimed. It's managed** (you don't deploy or tune the CA Deployment yourself - just the pool bounds).
- **No Karpenter equivalent** on OVH - there is no "provision an arbitrary right-sized instance" path; you scale the fixed pool between its min and max. This is exactly why the course defaults to Cluster Autoscaler: it's the lowest common denominator across both clouds.

## Further reading
- **KP "Elastic Scale"** - the pattern (HPA/VPA/Cluster Autoscaler together).
- **KIA ch15** - autoscaling pods and cluster nodes.
- Kubernetes docs: HorizontalPodAutoscaler (incl. `behavior`/stabilization), Cluster Autoscaler FAQ, Karpenter docs, VPA repo (`autoscaler/vertical-pod-autoscaler`), KEDA (`keda.sh`) for event-driven + scale-to-zero.
