# Lecture 14 - Scheduling: affinity, anti-affinity, topology spread, taints/tolerations: automated placement

## Answers to the lab checkpoints
- **(0) Yes - every node already carries `topology.kubernetes.io/zone` (and `kubernetes.io/hostname`, `topology.kubernetes.io/region`, instance/arch labels). The cloud provider's cluster-controller / kubelet set them**, not you. These "well-known" labels are the contract topology spread and zone-aware scheduling rely on; you never have to label zones yourself on a managed cluster. (You only added `course.disktype` / `course.dedicated` because those are *your* domain labels, not platform facts.)
- **(1)** All 3 `pinned` replicas land on **`$N1` and only `$N1` - it's the single node matching `course.disktype=ssd`. `nodeAffinity` does not** spread; it only filters *which nodes are eligible*. With one eligible node, all three pile onto it (subject to capacity). Yes, replicas happily share a node - nothing here says otherwise.
- **(2) On a 3-node cluster the 3 `spread-hostname` replicas occupy 3 distinct nodes (one each). On a 2-node cluster the 3rd replica doubles up** - it does **not** go Pending, because the rule is `preferredDuringScheduling...` (soft): the scheduler tries to honor it, and when it can't, it schedules anyway. (Contrast Break it, which uses the *hard* form.)
- **(3) With 4 replicas, `maxSkew=1`, and 3 zones, the only distribution that keeps the busiest zone within 1 of the emptiest is 2 / 1 / 1** (one zone gets 2, the others 1 each). Any `3 / 1 / 0` would violate `maxSkew=1`. The scheduler picks the lowest-count zone for each successive replica.
- **(4)** `no-toleration` **cannot** land on `$N2`. The `NoSchedule` taint repels every pod that doesn't tolerate it, so the scheduler filters `$N2` out for this pod. It lands on `$N1` (or another untainted node). On a 2-node cluster where `$N2` is tainted, it goes onto `$N1`.
- **(5)** `tolerated` lands on **`$N2`** - and it needs *both* pieces. The **toleration** removes the repulsion (without it, `$N2` is filtered out). But a toleration only grants *permission*; it doesn't *attract*. The `nodeAffinity` on `course.dedicated=gpu` is what actually pins it onto `$N2` rather than letting it drift to the untainted node. Taint = "stay away unless you tolerate me"; toleration = "I'm allowed"; affinity = "I want to be here."
- **(B1)** Exactly one replica per node becomes Running (3 on a 3-node cluster) and the **other 6 stay `Pending` forever. The anti-affinity is `requiredDuringScheduling...` (hard) with `topologyKey=hostname`, so no two replicas may share a node; once every node holds one, there is no eligible node left for the rest. They will never** schedule on their own - the constraint, not capacity, is the blocker.
- **(B2)** The scheduler emits `FailedScheduling` with a message like *"0/3 nodes are available: 3 node(s) didn't match pod anti-affinity rules"* (wording varies by version). It reports filtering out **all** nodes - each already runs a replica, so each fails the anti-affinity predicate in the filter phase. No score phase is ever reached because no node survives filtering.
- **(B3) After `scale --replicas=3` (or relaxing to `preferred`), the Pending pods become `Running` immediately. Nothing about the cluster changed - same nodes, same capacity - only the constraint** relaxed. This is the whole lesson: most "Unschedulable" incidents are over-constraint, not under-capacity.

---

## What just happened (under the hood)
The scheduler is a two-phase pure function over nodes: filter, then score, then bind. For each Pending pod the kube-scheduler runs:

1. **Filter (predicates) - "which nodes *can* run this pod?" It discards every node that fails a hard rule: insufficient allocatable `requests`, an untolerated taint, a failed `nodeSelector`/`required nodeAffinity`, a violated `required podAntiAffinity`, a `topologySpreadConstraint` with `whenUnsatisfiable: DoNotSchedule`, node not Ready, etc. If zero** nodes survive, the pod stays `Pending` and you get the `FailedScheduling` event from Break it.
2. **Score (priorities) - "of the survivors, which is *best*?"** Each remaining node gets a weighted score from plugins: `preferred` affinity/anti-affinity weights, topology-spread balance, least-loaded / most-allocated bin-packing, image locality, and more. Highest score wins.
3. **Bind. The scheduler writes `spec.nodeName` (a Binding). From that instant the kubelet on that node owns the pod - it pulls the image and starts containers (the lifecycle you saw in lab 01). Affinity is evaluated only at scheduling time** (`IgnoredDuringExecution`): a running pod is *not* evicted when labels later change. The descheduler exists precisely to fix that drift (below).

Hard vs soft is the single most important distinction.
- `requiredDuringSchedulingIgnoredDuringExecution` is a *filter* - a hard constraint. Unmet -> the node is rejected -> if no node qualifies the pod is `Pending` indefinitely. Use for correctness ("must be on an SSD/GPU/region node").
- `preferredDuringSchedulingIgnoredDuringExecution` is a *score weight* - a soft wish. Unmet -> the pod still schedules, just on a less-preferred node. Use for HA hints you don't want to turn into an outage.
This is why the lab's `preferred` anti-affinity doubled up on a small cluster while the Break-it `required` anti-affinity wedged Pending.

Taints and tolerations are the inverse of affinity. Affinity is the *pod* attracting itself to nodes. A **taint** is the *node* repelling pods: it says "stay away unless you tolerate me." A **toleration on a pod is opt-in permission - it removes the repulsion but does not** attract (that's why the lab's `tolerated` pod also needed `nodeAffinity` to actually land on the dedicated node). Three effects:
- **`NoSchedule`** - new pods without the toleration aren't placed here (existing pods stay).
- **`PreferNoSchedule`** - soft version; avoid if possible.
- **`NoExecute`** - also *evicts* already-running pods that don't tolerate it (with optional `tolerationSeconds`). This is how the node-lifecycle controller drains a `NotReady` node: it adds `node.kubernetes.io/not-ready:NoExecute` and pods without a toleration are evicted after the grace period.

`topologySpreadConstraints` is the modern, declarative spread - prefer it over hand-rolled anti-affinity. It states intent directly ("keep the per-zone replica counts within `maxSkew`"), scales to many domains cleanly, and is far cheaper for the scheduler than pairwise pod anti-affinity (which is O(pods²)-ish to evaluate). `maxSkew` is the allowed difference between the busiest and emptiest topology domain; `whenUnsatisfiable: DoNotSchedule` makes it a hard filter, `ScheduleAnyway` makes it a soft score. The lab used `ScheduleAnyway` so a small/imbalanced cluster never wedges you.

**The descheduler (concept).** Because affinity/spread are evaluated only at *scheduling* time, the cluster drifts: a node drains and its pods reschedule onto two zones; later capacity returns but the pods don't rebalance. The [descheduler](https://github.com/kubernetes-sigs/descheduler) is a separate component that periodically *evicts* pods violating current policy (`RemovePodsViolatingTopologySpreadConstraint`, `RemovePodsViolatingInterPodAntiAffinity`, `LowNodeUtilization`, ...) so the scheduler places them afresh. It rebalances; it never binds pods itself.

## Dev notes
- **Declare placement *intent*, never node names.** You labeled a node `course.disktype=ssd` and let `nodeAffinity` find it; you never wrote `nodeName: ip-10-...`. Hardcoding a node name is brittle (the node will be replaced) and defeats self-healing.
- Default to `preferred` and `topologySpreadConstraints` for HA hints; reserve `required` for genuine correctness constraints (hardware, data residency). A `required` rule is a latent outage if the matching nodes ever disappear.
- **`nodeSelector` is the 80% case.** It's just the simple equality form of `required nodeAffinity`. Reach for full `nodeAffinity` only when you need `In`/`NotIn`/`Exists` operators or `preferred` weighting.

## DevOps / Platform notes
- Dedicated node pools = taint the pool + label the pool. The taint keeps general workloads *off* the special nodes; the label lets the intended workloads *target* them. Bake both into the managed node group / OVH pool template (lab's EKS/OVH blocks) so replacement nodes are born correct - hand-tainting a node, as the lab does for speed, is lost the moment that node is recycled.
- **Node labels are an API contract.** Workloads' `nodeAffinity`/`nodeSelector` depend on them. Renaming or dropping a node label silently strands every pod that selects it. Treat the label scheme like an interface and version it.
- Managed pools carry zone/host labels for free. You build zone-aware HA on top of `topology.kubernetes.io/zone` without ever labeling zones yourself - that's the platform's job.

## Architect notes (trade-offs)
- Fault domains: spread to survive a domain loss. Spreading replicas one-per-node survives a single node failure; spreading across AZs (`topology.kubernetes.io/zone`) survives a whole-zone outage - the classic reason multi-AZ clusters exist. Pick the `topologyKey` that matches the failure you're insuring against (host vs zone vs region).
- Bin-packing density vs spread is a real tension. Tight packing (fill a node before using the next) is cheaper and helps the autoscaler scale to zero; spreading wastes a little capacity (you keep replicas on separate, partly-idle nodes) to buy resilience. The scheduler's default scoring leans toward balance; you tune it with spread constraints and pod (anti-)affinity per workload class, not one global policy.
- **Isolation via taints has a cost.** A dedicated pool that sits half-idle is stranded capacity unless something can burst onto it. Taints buy predictability (no noisy neighbor on the GPU box) at the price of utilization.

## SRE notes (failure modes, SLOs, toil)
- Spread so one node/zone loss can't take all replicas. Three replicas on one node is one `kubectl drain` away from a full outage despite "3/3 Ready." `topologySpreadConstraints` across zones is the cheapest availability win in the cluster - it's capacity-planning policy expressed in YAML.
- `Pending` / `Unschedulable` is a top incident class, and over-constraint beats under-capacity as the cause. When pods won't schedule, your *first* move is the scheduler's own events: `kubectl describe pod <p> | sed -n '/Events:/,$p'` -> read the `FailedScheduling` reason. "didn't match pod anti-affinity rules" / "didn't match node affinity/selector" / "had untolerated taint" point straight at the constraint; "Insufficient cpu/memory" points at capacity (and the autoscaler, lab 18). Alert on Pending-pod age and on `unschedulable`.
- A taint without a matching toleration silently strands workloads. Taint a pool for "special" use and forget to add the toleration to the workloads that belong there, and they pile up Pending with no obvious error until you read events. `NoExecute` is sharper: it *evicts* running pods - taint a busy node `NoExecute` by mistake and you cause an immediate eviction storm.
- Affinity is evaluated only at schedule time. After a node drain + return, spread can be stale; the cluster looks balanced in the manifest and is lopsided in reality. The descheduler (or a manual rolling restart) is the remediation - know which your platform runs.

## AI/ML notes (LLM/ML serving mapping - conceptual)
*(This course never requests a GPU - everything below is the conceptual mapping onto the exact mechanisms you just used.)*
- GPU node pools are tainted `nvidia.com/gpu:NoSchedule`. Only workloads that explicitly tolerate it (and request `nvidia.com/gpu`) land there; CPU-only pods are repelled so they don't waste scarce, expensive GPU nodes - exactly the dedicated-node pattern from steps 4-5, with `course.dedicated=gpu` standing in for `nvidia.com/gpu`.
- `nodeAffinity` to a specific GPU SKU. Inference servers pin to a model that fits the card: `nvidia.com/gpu.product In [NVIDIA-A100-SXM4-80GB]` (labels published by the NVIDIA GPU Feature Discovery DaemonSet). Wrong-card placement = OOM or wasted VRAM - the same "affinity must match labels that actually exist" rule.
- Gang / bin-pack scheduling for multi-GPU jobs. Tensor- and pipeline-parallel ranks need **all-or-nothing** placement: schedule all N workers or none (a half-placed job wastes GPUs and deadlocks on the collective). Vanilla kube-scheduler places pods independently, so platforms add a gang scheduler (Volcano, Kueue, the Kubernetes `coscheduling` plugin) on top.
- Topology/affinity for interconnect locality. Co-locating the ranks of one job on the same node (NVLink) or same rack (NVSwitch/IB leaf) slashes collective-communication latency. Expressed with `podAffinity` (`topologyKey=kubernetes.io/hostname` to force same-node) or custom rack labels - the same `topologyKey` knob you used for spread, run in reverse to *pack* instead of spread.

## Pitfalls
- Over-constraining -> Pending forever. `required` anti-affinity (or spread with `DoNotSchedule`) demanding more domains than exist is the classic self-inflicted outage. Prefer `preferred` / `ScheduleAnyway` unless the constraint is for correctness.
- `required` vs `preferred` confusion. Picking the hard form for a soft HA wish turns a degraded state into a full outage. Know which one you wrote.
- Taint without tolerations strands pods. Carve out a pool and forget the toleration on its intended workloads -> silent Pending. And `NoExecute` evicts running pods, not just blocks new ones.
- Affinity assuming labels that aren't there. `nodeAffinity` to a label no node carries -> unschedulable; a typo'd `topologyKey` -> no spread. Confirm the label exists on real nodes (`kubectl get nodes -L <label>`) before you ship the rule.
- Forgetting the topology label on nodes. `topologySpreadConstraints` only works if **every** candidate node carries the `topologyKey` label; an unlabeled node is treated as its own domain and quietly skews the spread.

## Further reading
- **KP "Automated Placement"** - the pattern: declare placement intent and let the scheduler decide; node/pod affinity, taints/tolerations, spread.
- **KIA ch16** - advanced scheduling: node selectors and affinity, pod affinity/anti-affinity, taints and tolerations, the scheduler's filter/score phases, and configuring placement for HA and isolation.
- Kubernetes docs: *Assigning Pods to Nodes*, *Taints and Tolerations*, *Pod Topology Spread Constraints*; the **descheduler** project (`kubernetes-sigs/descheduler`) for rebalancing drift.
