# Lecture 05 - Resource requests/limits, QoS & quotas: predictable demands

## Answers to the lab checkpoints
- **(0) Yes, it's admitted - with no `LimitRange`/`ResourceQuota` in the namespace, a pod with no `resources:` is perfectly legal. It gets QoS class BestEffort** (no requests and no limits anywhere). This is exactly the "unbounded pod" that the guardrails in Steps 6-7 exist to stop.
- **(1)** `qos-guaranteed` -> **Guaranteed** (requests == limits for *both* CPU and memory on the only container); `qos-burstable` -> **Burstable (it has requests, but they're below the limits); `qos-besteffort` -> BestEffort** (no requests, no limits).
- **(2)** QoS is a *function* of the resource fields. **Guaranteed requires every container to set both requests and limits, and request == limit for each resource. Burstable = at least one request/limit set but not meeting the Guaranteed bar. BestEffort** = nothing set anywhere. You never write `qosClass`; the API server computes it and stamps `status.qosClass`.
- **(3) `mem-hog` never stays Running. It goes `Running` -> killed -> `CrashLoopBackOff`, with `RESTARTS` climbing. `describe`/jsonpath show `lastState.terminated.reason: OOMKilled` and exit code 137** (128 + SIGKILL 9). Memory is **incompressible** - the kernel can't "slow down" RAM usage, so when the cgroup hits its hard limit the OOM-killer reaps the process.
- **(4)** It is **not killed. `stress --cpu 2` wants ~2000m but the 200m limit caps it: `kubectl top pod cpu-hog` reads ≈ 200m (0.2 cores) and `RESTARTS` stays 0. CPU is compressible** - the scheduler/kernel just hands it fewer time-slices (CFS throttling).
- **(4 - Prove it)** `cpu.stat` shows `nr_throttled` and `throttled_usec` (cgroup v2) - or `throttled_time` (v1) - *increasing* between two reads while the process keeps running. The pod is healthy by every K8s signal and simultaneously starved of CPU. That gap is the whole SRE lesson below.
- **(5)** Still `BestEffort`. Nothing about QoS changed; it's purely derived from the (absent) resource fields.
- **(6) The LimitRange's `defaultRequest` (`50m`/`64Mi`) and `default` (`200m`/`128Mi`) are injected at admission, so the running pod's `spec.containers[0].resources` is non-empty even though your YAML had none. Because requests (`50m`/`64Mi`) ≠ limits (`200m`/`128Mi`), the resulting QoS is Burstable**, not BestEffort. The LimitRange quietly upgraded a would-be BestEffort pod.
- **(7a)** Once a `ResourceQuota` that tracks `requests.*`/`limits.*` exists, a container with **no** requests is **rejected at admission** ("must specify requests.cpu, requests.memory") - *unless* a LimitRange defaults them in first. That's why you had to delete the LimitRange to actually see the rejection.
- **(7b) `quota-buster` is well-formed but too big: it asks for 800Mi `requests.memory` against a 512Mi namespace cap, so admission fails with `exceeded quota: lab-05-quota, requested: requests.memory=800Mi, used: ..., limited: requests.memory=512Mi`. The quota caps the aggregate**, not the per-pod size.
- **(7 - Prove it)** With the LimitRange gone, the bare `no-requests` pod is rejected because the quota makes requests mandatory; putting the LimitRange back lets it through (defaulted). Two distinct quota behaviors: *cap the total* and *force everyone to declare*.
- **(B1) Yes - raise the limit above the working set (256Mi > ~150Mi) and the loop stops; the pod settles `Running` with stable restarts. The OOM loop was never a bug in `stress`; it was a limit set below the real working set**. (Pod resource fields are largely immutable in-place, so you usually edit the manifest and re-create.)
- **(B2) `oom_score_adj` ranking, highest (killed first) to lowest (protected): BestEffort (≈1000) -> Burstable (between, scaled by request size) -> Guaranteed (-997)**. The kubelet evicts and the kernel OOM-kills in that order. Giving a critical pod Guaranteed QoS is how you put it at the back of the firing line.

---

## What just happened (under the hood)

Two kinds of resources, two kinds of failure. Kubernetes splits resources into:
- **Compressible (CPU): can be throttled instantly with no loss of correctness. Exceed your CPU limit and the Linux CFS bandwidth controller** simply gives your cgroup fewer microseconds per 100ms period (`cpu.max` = `quota period`). Your process runs *slower*; it never dies. That's why `cpu-hog` sat at 200m with 0 restarts.
- **Incompressible** (memory): can't be reclaimed on demand - a process that allocated 150Mi *needs* 150Mi. When the cgroup hits its memory limit the kernel's **OOM-killer** terminates a process (SIGKILL -> exit 137). That's why `mem-hog` looped. There is no "use less RAM, slowly" knob.

The scheduler bin-packs on `requests`, never `limits`. When the scheduler places a pod it subtracts the pod's *requests* from each node's allocatable capacity and filters/scores nodes on what's left. Limits are invisible to the scheduler - they only bound runtime behavior on the node the pod already landed on. This is the single most misunderstood fact in resource management: you can massively overcommit limits (sum of limits ≫ node capacity) and the scheduler won't blink, because it only ever reasoned about requests.

QoS class is derived and drives eviction. The API server computes `status.qosClass` from the resource fields (Guaranteed / Burstable / BestEffort). That class sets each container's `oom_score_adj` and the kubelet's eviction ranking. Under node memory pressure the kubelet evicts BestEffort first, then Burstable (ordered by how far over its requests it is), Guaranteed last. QoS is not something you ask for - it's a consequence of how honestly you declared your needs.

LimitRange vs ResourceQuota - per-object vs aggregate.
- **LimitRange** is a *mutating + validating* admission control on **each object**: it injects defaults when fields are missing and rejects containers outside `min`/`max`. It operates one container at a time.
- **ResourceQuota** is a *validating* admission control on the **namespace total: it sums requests/limits/object-counts across all pods and rejects anything that would push the sum past `hard`. Crucially, the moment a quota tracks `requests.cpu`/`requests.memory`, the API server requires every new container to specify those values** - otherwise it can't account for them. That's the "requests become mandatory" rule, and it's why LimitRange + ResourceQuota are deployed together: the quota forces declaration, the LimitRange provides sane defaults so humans don't have to annotate every pod.

## Dev notes
- Right-size from observed usage, not vibes. Run the workload, watch `kubectl top pod` over a representative period (and your own histograms / Prometheus if you have them), then set requests near the steady-state and limits with headroom. Guessing high wastes capacity (poor bin-packing); guessing low gets you OOMKilled or throttled.
- The memory limit must exceed your real working set + headroom (heap, page cache you touch, runtime overhead, GC slack). A JVM/Go service that's "usually 200Mi" can spike on a big request - a 256Mi limit is an OOM waiting for the wrong payload.
- Memory request ≈ memory limit is often the right call for incompressible RAM, because there's no graceful degradation: you either have the page or you're killed. CPU is the opposite (see Architect notes).

## DevOps / Platform notes
- LimitRange + ResourceQuota are your multi-tenant guardrails. Stamp every tenant namespace with both: the quota caps the blast radius of one team, the LimitRange defaults requests so a team *can't* accidentally ship a fleet of unbounded BestEffort pods that starve neighbors.
- **Defaulting is a policy lever.** Set `defaultRequest` deliberately - too generous and your cluster looks full while idle; too stingy and defaulted pods get throttled/evicted. `min`/`max` stop both "1m CPU" cargo-cult pods and "give me the whole node" pods.
- **Quota object-count caps matter too** (`pods`, `services`, `count/deployments.apps`): they stop runaway controllers/CI from creating thousands of objects and overloading the API server, independent of CPU/memory.

## Architect notes (trade-offs)
- **Bin-packing density vs isolation.** Tight requests pack more pods per node (cheaper) but leave less slack for spikes; generous requests isolate workloads but strand capacity. Requests are the dial; pick per workload class, not one global policy.
- **Should you set CPU *limits* at all? A genuine, ongoing debate. CPU limits cause CFS throttling even when the node has spare CPU - a latency-sensitive service can be throttled into p99 spikes while cores sit idle. Many mature shops set CPU requests only (so the scheduler reserves a floor) and omit CPU limits**, relying on requests + cgroup *shares* for fair contention. Memory limits, by contrast, you almost always keep (the alternative to an OOMKill is a noisy neighbor taking the node down).
- **PriorityClass + preemption** is the tier above QoS: it decides *which pending pod wins a contested node* and *which running pod gets preempted* to make room. QoS decides eviction order under pressure; PriorityClass decides scheduling order and preemption. Use both for critical-vs-batch tiering.

## SRE notes (failure modes, SLOs, toil)
- **OOMKills and CPU throttling are *hidden* latency/availability sources. A throttled pod is `Ready`, passing probes, serving traffic - and slow. Your dashboards say "healthy"; your users see p99 latency. Throttling is invisible to liveness/readiness** - you only find it in `cpu.stat` / `container_cpu_cfs_throttled_periods_total`. Alert on throttling ratio, not just on restarts.
- Eviction order is a reliability lever you control for free. Give the pods you cannot lose **Guaranteed** QoS and they're evicted last under node pressure; leave batch/best-effort work as BestEffort and it absorbs the pressure first. This is capacity-planning policy expressed in YAML.
- Watch `OOMKilled` and `Evicted` in events and `kube_pod_container_status_last_terminated_reason`. An `OOMKilled` loop (exit 137, climbing restarts) means *limit < working set* - bump the limit or fix the leak; it will not self-heal. `Evicted` pods mean node pressure - check node allocatable and the requests of co-located pods.
- Capacity planning is done in `requests`. Sum of requests vs node allocatable tells you real headroom; sum of *limits* tells you your worst-case overcommit. Track both.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- GPUs are a non-overcommittable extended resource. `nvidia.com/gpu` is an **integer** count - no millicores, no overcommit, and you must set `requests == limits` (Kubernetes enforces it for extended resources). One pod owns whole GPU(s); there's no CFS-style time-slicing in vanilla scheduling (MPS/MIG are separate mechanisms). *(This course never requests a GPU - this is the conceptual mapping only.)*
- VRAM behaves like incompressible memory. Exceed it and you get a **CUDA OOM** - the GPU analog of the `mem-hog` OOMKill: incompressible, no graceful degradation, the allocation just fails or the process dies. Right-sizing VRAM is exactly the "memory limit must exceed working set" lesson, on the device.
- KV-cache must be budgeted against VRAM. An inference server's KV-cache grows with concurrency × sequence length; size it (vLLM `--gpu-memory-utilization`, max-num-seqs/max-model-len) so peak cache + model weights + activations fit, or you OOM **mid-request** under load - the worst time. There is no "burst the GPU" the way CPU bursts; plan for the peak.

## Pitfalls
- **BestEffort in prod.** First evicted, no floor, no ceiling. Fine for throwaway batch; never for a service. A ResourceQuota that forces requests is the cheapest fix.
- Memory limit < working set -> OOM loop. The classic. It won't recover on its own; raise the limit or shrink the footprint.
- Assuming a CPU limit "protects latency." It does the opposite - it *adds* throttling latency, even with idle cores. Requests reserve; limits throttle.
- Forgetting that ResourceQuota makes requests mandatory. Add a quota and suddenly request-less pods are rejected cluster-team-wide. Ship a LimitRange in the same change to default them.
- Sizing requests to raw instance/flavor memory. Node *allocatable* is less (kube/system reserved + eviction threshold). Read it from `kubectl describe node`.

## Further reading
- **KP "Predictable Demands"** - the pattern: declare requests/limits so the platform can place and protect you; the whole point of this lab.
- **KIA ch14** - resource management: requests/limits, the three QoS classes, OOM behavior, LimitRange and ResourceQuota in depth, and how the scheduler uses requests.
