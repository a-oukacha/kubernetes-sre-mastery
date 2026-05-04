# Lab 11 - Singleton, PodDisruptionBudget & leader election · **Exercise**
**Patterns: Singleton Service + Ownership Election Source: KP "Singleton Service"; DDS "Ownership Election"; KIA 4 Est:** 55 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

## Objective
Run **"at most one" active instance reliably - even across pod and node failure - using a lease-based leader election, and make ordinary node maintenance (drain / cluster upgrade) non-disruptive** with a **PodDisruptionBudget**. Then see how an over-tight PDB turns a routine upgrade into a 3am page.

## Concepts exercised
- **at-least-once (`replicas: 1` Deployment - brief overlap possible during a rollout) vs at-most-once** (lease-based leader election / StatefulSet ordinal identity)
- `coordination.k8s.io/Lease`: `holderIdentity`, `renewTime`, `leaseDurationSeconds`
- leader election: **acquire** the lease, **renew** before it expires, **fail over** after it lapses
- `PodDisruptionBudget` (`minAvailable` / `maxUnavailable`) and the **eviction API**
- **voluntary disruption (drain, eviction, autoscaler, node-group upgrade) vs involuntary** (node crash)
- `kubectl drain` / `cordon` / `uncordon`

## Prerequisites
- Labs 01-02 done (labels, selectors, `describe`/events, Deployments & rollouts).
- A reachable cluster with **2-3 schedulable nodes** (`kubectl get nodes` -> 2-3 `Ready`). The drain demo needs at least 2 nodes. See `../00-cluster-setup/`.
- RBAC appears here only as the minimum a Lease needs (SA + Role + RoleBinding); the full treatment is **lab 19** - forward-reference is fine.

## Setup
Create a namespace **`lab-11-singleton-pdb-leader` and make it the default for the rest of this lab (so you don't have to pass `-n` every time). No LoadBalancer or volume is created in this lab - everything runs in-cluster and CPU-only. The only resource that escapes the namespace is a cordoned node** (you'll cordon one during the drain demo); Cleanup `uncordon`s it.

---

## Tasks

### 1. Deploy the three leader candidates and their RBAC
Apply the two provided manifests `manifests/leader-rbac.yaml` and `manifests/leader-deploy.yaml`, wait for the `leader-elector` Deployment to roll out (3 pods Ready), and list those pods showing which node each landed on.

**Predict (1):** Three identical pods all run the *same* contention script against the *same* Lease object. How many of them will end up claiming "I AM THE LEADER" at steady state? Why can't it be all three?

### 2. Watch exactly one pod win the lease
Tail the logs of all three candidate pods at once (selecting by their shared label), follow until you reach steady state (~20s), then stop.

**Observe (2): Across the three pods, how many print `I AM THE LEADER`** each cycle, and what do the other two print? Note the leader pod's name.

### 3. Read the Lease object - the source of truth
Inspect the `demo-singleton` Lease (a `coordination.k8s.io/Lease`): view it, then pull just its `spec`, and finally extract only its `holderIdentity` field.

**Prove it (3): The `holderIdentity` in the Lease equals exactly one** pod name - the same pod that logs leadership in step 2. The leadership claim and the Lease agree. Capture that pod name; you're about to kill it.

### 4. Kill the leader - watch failover happen
Read the current `holderIdentity` from the Lease, delete that exact pod, then watch the Lease's `holderIdentity` until it changes to a different pod name. (The Deployment will also replace the deleted pod.)

**Predict (4): The lease's `leaseDurationSeconds` is 15**. After you delete the leader, roughly how long until a *different* pod's name appears as `holderIdentity`, and why is there a delay at all (rather than instant takeover)?

### 5. Confirm a new leader took over
Re-read the Lease's `holderIdentity` and confirm against the candidate pods' logs which pod now claims leadership.

**Prove it (5):** `holderIdentity` is now a **different pod than the one you killed, and that pod's logs say `I AM THE LEADER`. Failover happened with no human action** - the lease lapsed, a standby grabbed it.

### 6. Deploy the web app + a sane PDB
Apply the `manifests/web-deploy.yaml` Deployment and the `manifests/web-pdb.yaml` PodDisruptionBudget, wait for 3 pods Ready, then inspect the `web` PDB and list the `web` pods with the node each runs on.

**Observe (6):** The `web` PDB has `minAvailable: 2`. With 3 healthy replicas, how many `ALLOWED DISRUPTIONS` do you expect, and what does that number mean for a drain?

### 7. Drain a node - the PDB paces the eviction
Identify a node that actually hosts a `web` pod, then drain only the `web` pods off it (ignore DaemonSets, delete emptyDir data, scope the drain to the `web` label).

**Predict (7):** `drain` first **cordons** the node, then evicts the web pod on it through the eviction API. Given `minAvailable: 2`, will this single eviction succeed immediately, or wait? What has to become true elsewhere in the cluster before the eviction is allowed?

**Observe (7b):** While the drain runs (or just after), watch the `web` pods until a replacement comes up Ready on another node. Did total `web` availability ever drop below 2 Ready? (That is the PDB's whole job.)

---

## Verify
Demonstrate success with observable signals: the Lease names exactly one `holderIdentity`, and only that one pod logs `I AM THE LEADER`; the `web` Deployment is back to 3/3 ready and its PDB's `ALLOWED DISRUPTIONS` has returned to 1.

yes Success = the Lease names exactly one holder; after you killed the leader the holder **changed; and `kubectl drain` evicted the web pod without** ever dropping below 2 Ready replicas.

Now un-cordon the node you drained so it can schedule again (you'll re-drain it in Break-it).

---

## Break it - an over-tight PDB hangs the upgrade forever
Replace the sane PDB with the over-tight one in `manifests/web-pdb-overtight.yaml` (it protects **100%** of replicas under the same name, so it replaces the `web` PDB), then attempt the same drain on a node hosting a `web` pod. Let it run ~30s, then stop it.

**Predict (B1):** With `minAvailable: 100%`, what is `ALLOWED DISRUPTIONS`, and what exact message does `drain` repeat? Will it *ever* complete on its own?

**Observe (B2):** The node is now **cordoned but undrained** - the eviction can never satisfy the budget, so `drain` retries forever. In the real world this is a node-group/node-pool rolling upgrade stuck at "draining node 1 of 30." Now reapply the sane `manifests/web-pdb.yaml`, confirm `ALLOWED DISRUPTIONS` is back to 1, and re-run the same drain - it should now complete.

**Prove it (B3): With the relaxed PDB the same `drain` command completes**. The only thing that changed was the budget - proving the hang was the PDB, not the cluster.

Observe (B4) - replicas=1 is NOT a true singleton. The web app is not a singleton; the leader-elector is. Reason about why a `replicas: 1` Deployment would *still* not give you at-most-once: during a rolling update (or an involuntary reschedule) the new pod can start **before** the old one is fully gone, so two can run briefly. (Predict what guarantees you'd need instead - the lecture covers lease vs StatefulSet ordinal.)

---

## Cleanup
First, un-cordon **any** node left `SchedulingDisabled` by the drain demo - check the nodes, uncordon the one you drained, and if you lost track of which one, uncordon every cordoned node. A forgotten cordon silently shrinks every later lab's capacity. Then delete the `lab-11-singleton-pdb-leader` namespace, which removes the Deployments, Service, Lease, RBAC and PDBs. No cloud LB/volume was created.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
