# Lab 15 - DaemonSet & node agents · **Exercise**
**Patterns:** Daemon Service **Source:** KIA 4; KP "Daemon Service" **Est:** 40 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations and manifest edits yourself; that *is* the skill. Attempt every task and write
> down your answer to every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done,
> [`solution.md`](solution.md) has the exact commands + the output you should have seen + every
> checkpoint answer. Then read [`lecture.md`](lecture.md) for the course.

## Objective
Run **exactly one pod per node for node-level concerns (log shipping, host metrics, networking) using a DaemonSet - and understand why a DaemonSet that doesn't match or doesn't tolerate a node leaves that node unmonitored**.

## Concepts exercised
- DaemonSet: one pod per matching node, placed by the **default scheduler** (since 1.17), so affinity/taints/preemption all apply
- `nodeSelector` / nodeAffinity to target a **subset** of nodes
- tolerations so the agent also covers **tainted** nodes (control-plane, dedicated pools)
- rolling DaemonSet update (`updateStrategy: RollingUpdate`, `maxUnavailable`)
- `PriorityClass` for node-critical infra
- node-level **observability coverage** as a property you can lose silently

## Prerequisites
- A multi-node cluster (2-3 `Ready` nodes). See `../00-cluster-setup/`.
- **Lab 01 (kubectl fluency, labels/selectors) and Lab 14** (taints & tolerations - reused here; this lab re-derives the taint command so you don't need 14's artifacts).

## Setup
Create a namespace **`lab-15-daemonset`** and make it the default for the rest of this lab (so you don't have to pass `-n` every time). Then apply the cluster-scoped `PriorityClass` from `manifests/priorityclass.yaml` - it is removed again in Cleanup.

**Predict (0):** A DaemonSet has no `replicas:` field. Before you deploy one - how does it decide *how many* pods to run? Record your current node count now so you can compare against it later.

---

## Tasks

### 1. Deploy the agent - one pod per node
Apply `manifests/ds-agent-all-nodes.yaml`, then inspect the `node-agent` DaemonSet and list its pods with their node placement shown.

**Predict (1):** You have N `Ready` nodes (from step 0). How many `node-agent` pods will exist, and on how many distinct nodes? Check the `DESIRED`/`CURRENT`/`READY` columns of the DaemonSet.

**Observe (1b): Look at the `NODE` column for each pod. Are there ever two** `node-agent` pods on the same node? Confirm each agent reports its own node by reading one line of log per pod.

### 2. A new node is covered automatically (reason about it)
You generally won't add a node by hand mid-lab. Reason about the mechanism instead, then verify it the cheap way: delete one `node-agent` pod and watch what the controller does to restore one-per-node coverage.

**Predict (2): When the Cluster Autoscaler adds a brand-new node tomorrow, what makes the agent appear there with no edit to the DaemonSet**? And when you just deleted a pod - did `DESIRED` change, and what put a pod back on that node?

### 3. Restrict to a subset with `nodeSelector`
A real platform often runs an agent only on *some* nodes (e.g. the GPU pool). Deploy the subset variant `manifests/ds-agent-subset.yaml` - it targets `agentpool=observed`, which **no node has yet** - then inspect the `node-agent-subset` DaemonSet.

**Predict (3a):** No node is labeled `agentpool=observed` yet. What is `DESIRED` for `node-agent-subset` right now?

Now add the `agentpool=observed` label to exactly one node, and watch the subset DaemonSet and its pods react.

**Observe (3b): `DESIRED`/`CURRENT` for `node-agent-subset` should now equal the number of nodes carrying the label. Which node got the pod? (Compare to the node you labeled.) The full `node-agent` from step 1 is still on all** nodes - only the subset DaemonSet narrowed.

### 4. Taint a node - watch the agent go MISSING there
Pick a *second* node and taint it `dedicated=special:NoSchedule`. The `node-agent` DaemonSet has no toleration for this taint - wait for its rollout to settle and re-list the `node-agent` pods by node.

**Predict (4): You tainted that node with `NoSchedule` and the `node-agent` DaemonSet does not** tolerate it. Is the existing pod on that node evicted? Will a *replacement* land there if that pod restarts? Count the `node-agent` pods now vs. step 1 - and ask: which node is now blind to your logs/metrics?

`NoSchedule` does not evict already-running pods (that's `NoExecute`). To make the blind spot unmistakable, force a reschedule by deleting the `node-agent` pod on the tainted node, then re-list.

**Observe (4b):** After the delete, does any `node-agent` pod come back on the tainted node? Did `DESIRED` for the DaemonSet drop by one? (DaemonSet `DESIRED` counts only nodes the pod can actually schedule onto.)

### 5. Cover the tainted node with a toleration
Apply `manifests/ds-agent-tolerant.yaml`, wait for its rollout, then inspect the `node-agent-tolerant` DaemonSet and list its pods by node.

**Prove it (5): The `node-agent-tolerant` DaemonSet tolerates `dedicated=special:NoSchedule`. Confirm it has a pod on every** node, *including* the tainted one - compare the list of nodes carrying a tolerant pod against the full node list; they should match. The tolerant agent covers the node the plain agent could not.

### EKS
- Your cluster already runs system DaemonSets: `aws-node` (the VPC CNI) and `kube-proxy` are DaemonSets - they appear on every node in `kube-system`. The EBS CSI **node** plugin is also a DaemonSet (the controller is a Deployment).
- A dedicated managed node group is the realistic place for `nodeSelector` (e.g. `eks.amazonaws.com/nodegroup=observed`) and for taints (node-group labels, `taints:` in the nodegroup spec). The subset/tolerant variants here mirror that pattern.

### OVH
- MKS runs its CNI (Cilium or Canal) and `kube-proxy` as DaemonSets, plus the **Cinder CSI** node plugin DaemonSet in `kube-system`.
- Label/taint a **node pool** in the OVH Manager (or directly on the node) to drive the subset/tolerant variants exactly as above.

---

## Verify
Demonstrate success with observable signals:
- The all-nodes agent runs one pod per schedulable node: its DaemonSet `DESIRED == CURRENT == READY`, and no node carries two `node-agent` pods.
- The subset agent count equals the number of labeled nodes.
- The tolerant agent has a pod on the tainted node.

yes Success = `node-agent` runs one pod per schedulable node; `node-agent-subset` runs only on labeled nodes; `node-agent-tolerant` runs on **every** node including the tainted one.

---

## Break it - reproduce BOTH node-level blind spots
A node agent fails *silently*: nothing crashes, you just stop seeing a node. Reproduce the two ways it happens.

Blind spot A - a `nodeSelector` that matches nothing. Patch `node-agent-subset` so its `nodeSelector` targets a label no node carries (e.g. `agentpool=does-not-exist`), then inspect the DaemonSet and its pods.

**Predict (B1): What is `DESIRED`/`CURRENT` now, and how many pods are running? Did anything error, or did the agent quietly cover nothing**?

Blind spot B - a tainted node with no matching toleration. You already saw this in step 4: the second node is tainted `dedicated=special:NoSchedule` and the plain `node-agent` does not tolerate it. Confirm there is no `node-agent` pod on that node.

**Observe (B2):** The plain `node-agent` has no pod on the tainted node. If this were fluent-bit, that node's logs would never ship - and you'd discover it mid-incident. Which DaemonSet here *does* cover that node, and what one field makes the difference?

**Fix both.** Patch the `node-agent-subset` selector back to the real `agentpool=observed` label, and confirm the tolerant DaemonSet from step 5 already covers the tainted node - verify its distinct-node coverage matches the cluster's node count.

**Prove it (B3):** After the fix, `node-agent-subset` is back on the labeled node(s), and `node-agent-tolerant` covers as many distinct nodes as the cluster has. No blind spots.

---

## Cleanup
Undo everything that outlives the namespace, in this order:
- Remove the `agentpool=observed` **label from the first node and the `dedicated=special:NoSchedule` taint** from the second node - these are node-scoped and will *not* disappear with the namespace.
- Delete the `lab-15-daemonset` namespace (all DaemonSets/pods go with it).
- Delete the cluster-scoped `node-agent-critical` **PriorityClass** (it is not in the namespace).

No cloud LB/volume was created in this lab. **Double-check** that the label and taint are gone - leftover taints make later labs' pods mysteriously `Pending`.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
