# Lab 14 - Scheduling: affinity, anti-affinity, topology spread, taints/tolerations · **Exercise**
**Patterns:** Automated Placement **Source:** KIA 16; KP "Automated Placement" **Est:** 55 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations and the placement reasoning yourself; that *is* the skill. Attempt every task and
> write down your answer to every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done,
> [`solution.md`](solution.md) has the exact commands + the output you should have seen + every
> checkpoint answer. Then read [`lecture.md`](lecture.md) for the course.

## Objective
Control **where pods land. You will pin pods to a labeled node (`nodeAffinity`), spread replicas one-per-node (`podAntiAffinity`) and balanced across availability zones (`topologySpreadConstraints`), and carve out a dedicated node with a taint that only pods carrying the matching toleration** may use. Then you will over-constrain a Deployment on purpose and watch replicas wedge `Pending`/`Unschedulable` forever - the most common self-inflicted scheduling outage.

## Concepts exercised
- `nodeSelector` and `nodeAffinity` (`requiredDuringScheduling...` = hard vs `preferredDuringScheduling...` = soft)
- `podAffinity` / `podAntiAffinity` with `topologyKey` (`kubernetes.io/hostname`)
- `topologySpreadConstraints` (`maxSkew`, `topologyKey=zone`, `whenUnsatisfiable`)
- taints (`NoSchedule` / `PreferNoSchedule` / `NoExecute`) + tolerations
- the scheduler's **filter -> score -> bind** phases; hard (Pending if unmet) vs soft (best-effort)
- the descheduler (concept - see lecture)

## Prerequisites
- Labs 01-02 done (kubectl fluency, `apply`/`describe`/`get -o wide`/`get -o jsonpath`).
- A reachable **multi-node** cluster (2-3 `Ready`, ideally across multiple AZs - the course cluster is). See `../00-cluster-setup/`.
- Permission to **label and taint nodes** (confirm you can patch nodes before starting).

## Setup
Create a namespace **`lab-14-scheduling`** and make it the default for the rest of this lab. Then capture your node names alongside their `topology.kubernetes.io/zone` labels - you will reference both throughout. Pick one node to play the role of a "fast disk" node and a second node to become a dedicated node later; keep their names handy (exporting them as shell variables like `N1` and `N2` saves typing).

**Predict (0):** Inspect the node labels. Does **every** node already carry a `topology.kubernetes.io/zone` label without you doing anything? (Who set it?)

---

## Tasks

### 1. Label a node, then pin pods to it with nodeAffinity (HARD)
Label your first node with `course.disktype=ssd`. Apply `manifests/deploy-nodeaffinity.yaml` (3 replicas, a **required** `nodeAffinity` on `course.disktype=ssd`) and list its pods with their node placement.

**Predict (1):** With a `requiredDuringScheduling...` rule on `course.disktype=ssd`, which node(s) will the 3 replicas land on? Could two replicas share one node?

**Prove it (1):** Show that every `pinned` replica's node is your labeled node and nothing else.

### 2. Spread one replica per node with podAntiAffinity (SOFT)
Apply `manifests/deploy-antiaffinity.yaml` and list its pods with placement.

**Predict (2):** This Deployment uses **preferred** (soft) anti-affinity with `topologyKey=kubernetes.io/hostname` and 3 replicas. On a 3-node cluster, how many distinct nodes will the replicas occupy? On a 2-node cluster, what happens to the 3rd replica - Pending, or does it double up?

**Prove it (2):** Count the distinct nodes the `spread-hostname` replicas actually occupy.

### 3. Balance replicas across AZs with topologySpreadConstraints
Apply `manifests/deploy-topologyspread.yaml` and list its pods with placement.

**Predict (3):** This Deployment has 4 replicas, `maxSkew=1`, `topologyKey=topology.kubernetes.io/zone`. On a 3-AZ cluster, how will 4 replicas distribute across the 3 zones? (How even can it be with `maxSkew=1`?)

**Prove it (3): For each `spread-zone` pod, resolve its node's zone label and tally the count per zone. The per-zone counts should differ by at most 1** (e.g. `2 / 1 / 1` across three zones).

### 4. Taint a node - an untolerated pod AVOIDS it
Taint your second node with `course.dedicated=gpu:NoSchedule` so it repels everything that does not explicitly tolerate it. Also add the label `course.dedicated=gpu` to that node, so an affinity rule can target it by name in step 5. Confirm the taint landed, then apply `manifests/pod-no-toleration.yaml` (a pod carrying **no** toleration) and observe where it schedules.

**Predict (4):** With no toleration, can `no-toleration` land on the tainted node? Which node will it show instead?

**Prove it (4):** Show that `no-toleration`'s node is never your tainted node.

### 5. Add the matching toleration - now the pod CAN land on the tainted node
Apply `manifests/pod-toleration.yaml` and observe where it schedules.

**Predict (5): `tolerated` carries the toleration for `course.dedicated=gpu:NoSchedule` and a `nodeAffinity` requiring `course.dedicated=gpu` (the label you added in step 4). Where does it land, and why are both** pieces needed?

**Prove it (5):** Show that `tolerated` scheduled onto the tainted, dedicated node.

---

## Verify
Demonstrate success with observable signals: all `pinned` replicas sit only on your labeled node; `spread-hostname` replicas occupy distinct nodes (one per node); `spread-zone` replicas differ by at most 1 per zone; and the `no-toleration` pod is **not on the tainted node while `tolerated` is**.

yes Success = `pinned` only on the labeled node; `spread-hostname` one-per-node; `spread-zone` differs by at most 1 per zone; `no-toleration` is **not on the tainted node while `tolerated` is**.

---

## Break it - over-constrain scheduling into permanent Pending
A HARD one-per-node anti-affinity with more replicas than nodes is unsatisfiable: the surplus replicas have no eligible node and wedge `Pending` forever. Apply `manifests/deploy-overconstrained.yaml` and list its pods - you should see some `Running` and the rest `Pending`.

**Predict (B1):** This Deployment asks for **9** replicas with `requiredDuringScheduling` anti-affinity (`topologyKey=hostname`). On a 3-node cluster, how many become `Running` and how many stay `Pending`? Will the Pending ones ever schedule on their own?

Debug it the right way - events from the scheduler, not logs. Pick one of the Pending pods and read its events.

**Observe (B2):** Read the `FailedScheduling` message. Which phrase names the cause? How many nodes did the scheduler report it filtered out, and why?

**Prove it (B3): Relax the constraint and watch the Pending pods recover - either reduce the replica count to the node count, or switch the anti-affinity rule from required (hard) to preferred (soft) and re-apply. Convince yourself the formerly-Pending pods are now `Running` - nothing about the cluster changed except the constraint you relaxed**.

---

## Cleanup
Delete the `lab-14-scheduling` namespace. Then **undo every node mutation you made - the label on your first node, plus the label and the taint on your second node. These are cluster-scoped, so deleting the namespace does not** remove them; leaving them behind will silently affect later labs. Verify nothing of yours remains on the nodes afterward. No node was drained or cordoned, so there is nothing to uncordon; no cloud LB/volume was created.

---

### EKS
- **Zone labels are free.** Managed node groups stamp `topology.kubernetes.io/zone` (and `kubernetes.io/hostname`) automatically, so step 3's topology spread works out of the box on a multi-AZ cluster (`eks-cluster.yaml` spans 3 AZs). Confirm by listing nodes with the zone label shown as a column.
- Dedicated pools = a tainted managed node group. Instead of tainting a node by hand, bake the `course.dedicated=gpu` label and the `NoSchedule` taint into the `eksctl` managed node group definition so new and replacement nodes are born tainted. Real GPU groups taint `nvidia.com/gpu:NoSchedule` so only GPU workloads (with the toleration) land there - see the AI/ML notes in the lecture.

### OVH
- **Node pools carry zone labels too.** A multi-AZ MKS pool spreads its nodes across the region's zones and labels them `topology.kubernetes.io/zone`; step 3 works unchanged. Verify by listing nodes with the zone label shown as a column.
- Label & taint at the pool, not the node. OVH pools support `labels` and `taints` (Manager UI, or Terraform `ovh_cloud_project_kube_nodepool` `template.metadata.labels` / `template.spec.taints`). Set `course.dedicated=gpu` plus the `NoSchedule` taint on a dedicated pool so the pool's autoscaler reproduces them on every new node - hand-tainting a single node (as this lab does for speed) is lost when that node is replaced.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
