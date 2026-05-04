# Lecture 11 - Singleton, PodDisruptionBudget & leader election: at-most-once and surviving maintenance

## Answers to the lab checkpoints
- **(1)** **Exactly one.** All three pods race for the *same* Lease object, but the Lease has a single `holderIdentity` field and writes to it use optimistic concurrency (compare-and-swap on `resourceVersion`). When two candidates try to grab a free lease at the same instant, the API server accepts only the first write; the second sees a `resourceVersion` conflict and loses. So at most one pod can be the holder at any moment - it can't be all three by construction.
- **(2)** One pod prints `I AM THE LEADER` every cycle (it re-renews and re-logs); the other two print `standing by (current leader is '<name>')`. The standbys never do the leader's work - they only watch the lease and wait for it to lapse.
- **(3)** `holderIdentity` equals the one pod that logs leadership. The log line and the Lease are two views of the same fact: the Lease *is* the source of truth, and the log is just that pod observing that it holds it.
- **(4)** Roughly `leaseDurationSeconds` (15s) plus a renew interval, so ~15-20s. The delay is the whole point of a lease: a standby must not steal the lease the instant a renew is missed (the holder might just be slow or briefly partitioned). It waits until the lease has provably **lapsed** - `renewTime` older than `leaseDurationSeconds` - before taking over. That window is the deliberate trade between fast failover (short lease) and stability (long lease).
- **(5)** `holderIdentity` is now a *different* pod (the Deployment also created a fresh pod to replace the deleted one, but the new *holder* is simply whichever standby grabbed the lapsed lease first - it may even be one of the survivors, not the brand-new pod). That pod's logs now say `I AM THE LEADER`. No human did anything; the lease mechanism handled it.
- **(6)** **1 allowed disruption. `minAvailable: 2` with 3 healthy replicas means the eviction API will let you take 3 − 2 = 1** pod down voluntarily at a time. `ALLOWED DISRUPTIONS: 1` is the live budget - evict one, it drops to 0 until a replacement is Ready, then back to 1.
- **(7)** It **waits, then succeeds.** `drain` cordons the node (no new pods land there) and calls the eviction API on the web pod. The eviction is allowed only while it keeps `>= 2` Ready. If evicting this pod would drop to Ready=2 *and stay there* that's fine (2 ≥ 2); the eviction proceeds, the Deployment schedules a replacement on another node, and once that replacement is Ready the budget is whole again. On a small cluster you may see it pause until the replacement is Ready elsewhere.
- **(7b)** No - total Ready web pods never drops below 2. That is exactly the guarantee a PDB buys you during voluntary disruption.
- **(B1) `ALLOWED DISRUPTIONS: 0`. `drain` repeats `error when evicting pods/"web-..." ... Cannot evict pod as it would violate the pod's disruption budget` and retries on a backoff forever**. It never completes on its own - `minAvailable: 100%` means *no* web pod may ever be voluntarily evicted.
- **(B2)/(B3)** The node sits **cordoned but undrained**. Relaxing the PDB back to `minAvailable: 2` immediately raises `ALLOWED DISRUPTIONS` to 1, and the identical `drain` command now completes. The only variable was the budget - proving the hang was the PDB, not a broken cluster or node.
- **(B4)** A `replicas: 1` Deployment is **at-least-once, not at-most-once.** During a rolling update the new pod can become Ready *before* the old pod's `SIGTERM`/grace period finishes, so two instances overlap for seconds. An involuntary reschedule (node declared dead) can do the same - the old pod may still be running on a partitioned node while a replacement starts. For true at-most-once you need either a **lease (the holder is the only one allowed to act) or a StatefulSet ordinal** (stable identity, ordered start/stop, at most one pod per ordinal).

---

## What just happened (under the hood)
You exercised two independent reliability primitives that are constantly confused for each other.

1. A Lease is time-bounded ownership. `coordination.k8s.io/Lease` is a tiny object: `holderIdentity` (who owns it), `renewTime` (when they last proved liveness), and `leaseDurationSeconds` (how long that proof is valid). Leader election is a loop every candidate runs:
1. Read the Lease.
2. If I hold it -> write a fresh `renewTime` (renew). The write is a compare-and-swap on `resourceVersion`: if someone else wrote since I read, my write is rejected and I re-read.
3. If it's free or **lapsed** (`now − renewTime > leaseDurationSeconds`) -> try to grab it with the same CAS write.
4. Otherwise -> stand by.

Because the API server serializes writes to a single object and rejects stale `resourceVersion`s, **at most one candidate can be the holder at any instant. This is not a toy: kube-controller-manager** and **kube-scheduler** run several replicas for HA and use exactly this Lease mechanism to ensure only one is *active* - the others are hot standbys. You reimplemented their core in a shell script.

The cost is a **failover window**: a standby must wait out `leaseDurationSeconds` before stealing a lapsed lease, so the system has no active holder for up to that long after the leader dies. Short lease = fast failover but more flapping risk under load/partition; long lease = stable but slow failover. That trade is the only real tuning knob.

**2. A PodDisruptionBudget bounds *voluntary* disruption. A PDB is not a scheduler input and it does not protect running pods from crashes. It is a rule the eviction API consults. `kubectl drain`, the cluster autoscaler, and managed node-group/node-pool upgrades don't `delete` pods - they call eviction, which is gated: an eviction is allowed only if, afterwards, the PDB-selected set still satisfies `minAvailable` (or stays under `maxUnavailable`). If not, the eviction returns `429 TooManyRequests` and the caller retries. `drain` retries on a backoff, which is why an over-tight PDB makes it hang rather than fail**.

The crucial distinction the lab burned in:
- **Voluntary disruption - drain, eviction, autoscaler scale-down, node-group upgrade. PDB applies.** You control the pace.
- **Involuntary disruption - node crash, kernel panic, hardware loss, OOM-killed kubelet. PDB does NOT apply.** Nobody asked permission; the pods are just gone. You design for this with replicas across nodes/zones, not with a PDB.

A PDB protects you from *yourself and your automation*, not from the universe.

## Dev notes
- Make leader work idempotent and re-entrant. You may briefly have two leaders (failover window, clock skew) and you may **lose** leadership mid-task (lease stolen because your renew was slow). Design the work so a duplicate or interrupted run is harmless: idempotent writes, fencing tokens, "claim then verify-still-holder before each side effect."
- Renew on a timer, and stop work the instant you lose the lease. A correct elector checks "do I still hold it?" *before* every consequential action, not just at startup. The dangerous bug is a former leader that keeps writing after it's been demoted.
- **Don't hand-roll this in production.** Use `client-go`'s `leaderelection` package (Go) or your language's K8s client equivalent - it handles the CAS, jitter, and `OnStoppedLeading` callbacks correctly. The shell script here is for *seeing* the mechanism, not shipping it.
- `terminationGracePeriodSeconds` matters for singletons. If your singleton holds a lease and is being terminated (rollout, drain), use the grace period to release the lease cleanly so the next holder takes over fast instead of waiting the full `leaseDurationSeconds`.

## DevOps / Platform notes
- Every safe node operation depends on correct PDBs. `kubectl drain`, the cluster autoscaler's scale-down, and - most importantly - **managed control-plane/node upgrades** (EKS managed node groups, OVH node-pool rolling upgrades) all evict through the same API and all respect PDBs. A workload with no PDB can be wiped to zero during an upgrade; a workload with a too-tight PDB blocks the upgrade entirely.
- Author a PDB for every multi-replica workload that matters, and size it as a percentage of replicas, not an absolute that breaks when you scale down (`minAvailable: 50%` survives a scale-to-2 better than `minAvailable: 2`). But beware: `minAvailable: 100%` or `minAvailable == replicas` is the classic foot-gun (Break-it).
- **`drain` flags you'll always need: `--ignore-daemonsets` (DaemonSet pods can't be evicted meaningfully - lab 15), `--delete-emptydir-data` (acknowledge ephemeral data loss), and optionally `--disable-eviction` (uses delete, bypasses PDBs** - emergency only). In the lab we added `--pod-selector` to keep the demo focused; real drains evict everything.
- **`cordon` ≠ `drain`.** `cordon` only stops *new* scheduling; existing pods stay. `drain` = cordon + evict. Always `uncordon` when done - a forgotten cordon silently removes a node from the schedulable pool (the lab's Cleanup hunts for stragglers).

### EKS
Node drain works identically. The real-world driver is **managed node group** upgrades / AMI rotations: EKS cordons and drains each node, honoring PDBs, before replacing it. An over-tight PDB will stall a managed node-group update (you'll see the update stuck and nodes stuck `cordoned`). Same goes for Karpenter consolidation, which also evicts via the API and respects PDBs.

### OVH
OVH Managed Kubernetes (MKS) **node-pool rolling upgrades** behave the same way: each node is cordoned and drained respecting PDBs before the new node image is rolled in. The mechanism is identical to EKS - drain + eviction API + PDB - so nothing in this lab changes between clouds; only the *trigger* (who initiates the drain) differs.

## Architect notes (trade-offs)
- Active-passive coordination is the K8s-native answer to "only one should act." Instead of designing a system that physically can only have one instance (fragile - it can't fail over), run N instances and let a **lease** elect one active leader. Identity is logical (who holds the lease), not physical (which pod exists).
- **Lease vs StatefulSet ordinal.** Lease = lightweight, fast to reason about, ideal when any replica can become the active one (controllers, schedulers, coordinators). StatefulSet ordinal = when each instance needs *stable storage and identity* (`pod-0` is always `pod-0`, lab 10) - at-most-one-per-ordinal is structural, not lease-based.
- Avoid hard-coded singletons that can't fail over (a single Deployment `replicas: 1` with `RollingUpdate` is neither a guaranteed singleton *nor* highly available). If you truly need at-most-one with no overlap, a `replicas: 1` Deployment with `strategy: Recreate` gets you no overlap **but** with a downtime gap on every update - a lease usually beats it.
- PDB is a capacity-vs-availability contract with the platform. Tight PDB = strong availability guarantee but slow/blocked maintenance. Loose PDB = fast maintenance but bigger blast radius per operation. Choose deliberately per workload tier.

## SRE notes (failure modes, SLOs, toil)
- Node maintenance without an outage = PDB + (for singletons) leader election. That's the combination that lets you patch kernels and upgrade Kubernetes during business hours instead of a 2am window.
- Over-tight PDBs are a top cause of stuck cluster upgrades - the literal 3am page is "the node-group upgrade has been draining node 1 for 40 minutes." First diagnostic: `kubectl get pdb -A` and look for `ALLOWED DISRUPTIONS: 0` on a workload sitting on the node being drained. The fix is almost always relaxing one PDB, not touching the node.
- A PDB with `ALLOWED DISRUPTIONS: 0` is not always a bug - it can mean the workload is *already degraded* (a pod is unhealthy, so the budget has no slack). Check whether the workload is at full Ready replicas before blaming the PDB value.
- PDB protects voluntary disruption ONLY. Your availability SLO must also survive involuntary loss: spread replicas across nodes and zones (topology spread, lab 14; anti-affinity), and don't let a single node failure breach `minAvailable`. A PDB of `minAvailable: 2` is meaningless if all 3 replicas sit on one node a crash takes out.
- Failover-window math feeds your SLO. With `leaseDurationSeconds: 15`, a singleton's worst-case "no active leader" gap is ~15-20s per leader loss. If that's inside your error budget, fine; if not, shorten the lease (accepting more flap risk) or make the work tolerate the gap.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- A single coordinator must be a singleton-with-failover, not a hard singleton. Batch-inference and training-job orchestration typically have *one* component that assigns work - a job scheduler, a **Ray head**, a driver that partitions a dataset. Two active assigners would double-dispatch work or corrupt accounting. Lease-based ownership election is the K8s-native way to guarantee one active assigner while keeping hot standbys for fast recovery.
- Only one writer to a shared checkpoint. When several training/serving processes share a checkpoint or model-registry slot, electing a single leader to perform the write avoids torn/duplicated checkpoints. The fencing-token discipline from Dev notes matters most here: a demoted leader must not finish a stale checkpoint write.
- **PDBs protect rolling GPU-node maintenance of *replicated* inference servers** (vLLM/KServe replicas). When the platform drains a GPU node to patch a driver, a PDB keeps enough model replicas serving so TTFT/throughput SLOs hold during the maintenance - the same mechanism you saw with agnhost, just with expensive pods. (Still CPU-only here; no GPU needed to understand it.)
- Failover window = a brief scheduling stall, not data loss, *if* the coordinator's work is idempotent. A new Ray head or job manager re-reads cluster/job state from the lease-protected source of truth and resumes - which is exactly why idempotent, re-entrant coordinator logic is non-negotiable for ML control planes.

## Pitfalls
- Over-tight PDB stalls upgrades forever. `minAvailable: 100%` / `== replicas` -> `ALLOWED DISRUPTIONS: 0` -> drain and node-group upgrades hang indefinitely. Always leave at least one pod's worth of slack.
- **`replicas: 1` ≠ true singleton.** Rolling updates and involuntary reschedules can briefly run two. Use a lease or StatefulSet ordinal for real at-most-once.
- **PDB doesn't help on a node crash.** It's voluntary-disruption only. Spread replicas across nodes/zones for involuntary protection.
- Non-idempotent leader work corrupts state on failover. Two-leaders-briefly + a non-idempotent write = duplicated or torn side effects. Make the work safe to run twice or to be cut off mid-flight.
- **Lease duration mis-tuned.** Too short -> flapping leadership under load/partition (constant churn, thrashed work). Too long -> slow failover (a long no-leader gap after the holder dies).
- **PDB selector drift.** A PDB selects pods by label independently of any Deployment. If its `selector` doesn't match the pods, it silently protects nothing - verify `kubectl get pdb` shows the expected `ALLOWED DISRUPTIONS` and current/desired counts.
- **Forgotten `cordon`.** A drained-but-never-uncordoned node stays `SchedulingDisabled` and quietly shrinks cluster capacity. Always `uncordon`.

## Further reading
- **KP "Singleton Service"** - out-of-application (lease/leader-election) vs in-application singletons, and the PDB interaction.
- **DDS "Ownership Election"** (Burns) - leader election as a distributed-systems primitive and why time-bounded leases beat consensus for this use.
- **KIA ch4** - ReplicationControllers/ReplicaSets/Deployments and the replica model that frames at-least-once vs at-most-once.
- Kubernetes docs: *Specifying a Disruption Budget*, *Safely Drain a Node*, and the `coordination.k8s.io/Lease` API - plus `client-go`'s `tools/leaderelection` package for the production-grade implementation.
