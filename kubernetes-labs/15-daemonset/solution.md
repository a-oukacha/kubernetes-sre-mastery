# Lab 15 - DaemonSet & node agents · **Solution**
**Patterns:** Daemon Service **Source:** KIA 4; KP "Daemon Service" **Est:** 40 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
- A multi-node cluster (`kubectl get nodes` -> 2-3 `Ready`). See `../00-cluster-setup/`.
- **Lab 01 (kubectl fluency, labels/selectors) and Lab 14** (taints & tolerations - reused here; this lab re-derives the taint command so you don't need 14's artifacts).

## Setup
```bash
kubectl create namespace lab-15-daemonset
kubens lab-15-daemonset                 # or add -n lab-15-daemonset to every command

kubectl apply -f manifests/priorityclass.yaml      # cluster-scoped; removed in Cleanup
```
**Predict (0):** A DaemonSet has no `replicas:` field. Before you deploy one - how does it decide *how many* pods to run? Note your node count now: `kubectl get nodes` (you'll compare against it).

---

## Steps

### 1. Deploy the agent - one pod per node
```bash
kubectl apply -f manifests/ds-agent-all-nodes.yaml
kubectl get ds node-agent
kubectl get pods -o wide -l app=node-agent
```
**Predict (1):** You have N `Ready` nodes (from step 0). How many `node-agent` pods will exist, and on how many distinct nodes? Check the `DESIRED`/`CURRENT`/`READY` columns of `get ds`.

**Observe (1b): In `get pods -o wide`, list the `NODE` column. Are there ever two** `node-agent` pods on the same node? Confirm each agent reports its own node:
```bash
kubectl logs -l app=node-agent --prefix --tail=1
```

### 2. A new node is covered automatically (reason about it)
You generally won't add a node by hand mid-lab. Reason about the mechanism instead, then verify it the cheap way by deleting a pod.
```bash
# Delete one agent pod; the controller must restore one-per-node.
kubectl delete pod -l app=node-agent --field-selector spec.nodeName=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
kubectl get pods -o wide -l app=node-agent
```
**Predict (2): When the Cluster Autoscaler adds a brand-new node tomorrow, what makes the agent appear there with no edit to the DaemonSet**? And when you just deleted a pod - did `DESIRED` change, and what put a pod back on that node?

### 3. Restrict to a subset with `nodeSelector`
A real platform often runs an agent only on *some* nodes (e.g. the GPU pool). Deploy the subset variant - it targets `agentpool=observed`, which **no node has yet**.
```bash
kubectl apply -f manifests/ds-agent-subset.yaml
kubectl get ds node-agent-subset
```
**Predict (3a):** No node is labeled `agentpool=observed` yet. What is `DESIRED` for `node-agent-subset` right now?

Now label exactly one node and watch:
```bash
NODE1=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
kubectl label node "$NODE1" agentpool=observed
kubectl get ds node-agent-subset
kubectl get pods -o wide -l app=node-agent-subset
```
**Observe (3b): `DESIRED`/`CURRENT` for `node-agent-subset` should now equal the number of nodes carrying the label. Which node got the pod? (Compare to `$NODE1`.) The full `node-agent` from step 1 is still on all** nodes - only the subset DaemonSet narrowed.

### 4. Taint a node - watch the agent go MISSING there
```bash
NODE2=$(kubectl get nodes -o jsonpath='{.items[1].metadata.name}')
kubectl taint node "$NODE2" dedicated=special:NoSchedule
# The agent has no toleration for this taint:
kubectl rollout status ds/node-agent --timeout=60s
kubectl get pods -o wide -l app=node-agent
```
**Predict (4): You tainted `$NODE2` with `NoSchedule` and the `node-agent` DaemonSet does not** tolerate it. Is the existing pod on `$NODE2` evicted? Will a *replacement* land there if that pod restarts? Count the `node-agent` pods now vs. step 1 - and ask: which node is now blind to your logs/metrics?

> `NoSchedule` does not evict already-running pods (that's `NoExecute`). To make the blind spot unmistakable, force a reschedule on the tainted node:
```bash
kubectl delete pod -l app=node-agent --field-selector spec.nodeName="$NODE2"
kubectl get pods -o wide -l app=node-agent
```
**Observe (4b):** After the delete, does any `node-agent` pod come back on `$NODE2`? `DESIRED` for the DaemonSet - did it drop by one? (DaemonSet `DESIRED` counts only nodes the pod can actually schedule onto.)

### 5. Cover the tainted node with a toleration
```bash
kubectl apply -f manifests/ds-agent-tolerant.yaml
kubectl rollout status ds/node-agent-tolerant --timeout=60s
kubectl get ds node-agent-tolerant
kubectl get pods -o wide -l app=node-agent-tolerant
```
**Prove it (5): The `node-agent-tolerant` DaemonSet tolerates `dedicated=special:NoSchedule`. Confirm it has a pod on every** node, *including* `$NODE2`:
```bash
kubectl get pods -o wide -l app=node-agent-tolerant --no-headers | awk '{print $7}' | sort
kubectl get nodes -o jsonpath='{.items[*].metadata.name}'; echo
```
The two node lists should match. The tolerant agent covers the node the plain agent could not.

### EKS
- Your cluster already runs system DaemonSets: `aws-node` (the VPC CNI) and `kube-proxy` are DaemonSets - `kubectl get ds -n kube-system` shows them on every node. The EBS CSI **node** plugin is also a DaemonSet (the controller is a Deployment).
- A dedicated managed node group is the realistic place for `nodeSelector` (e.g. `eks.amazonaws.com/nodegroup=observed`) and for taints (`eksctl ... --node-labels`, `taints:` in the nodegroup spec). The subset/tolerant variants here mirror that pattern.

### OVH
- MKS runs its CNI (Cilium or Canal) and `kube-proxy` as DaemonSets, plus the **Cinder CSI** node plugin DaemonSet - `kubectl get ds -n kube-system`.
- Label/taint a **node pool** in the OVH Manager (or `kubectl label/taint node`) to drive the subset/tolerant variants exactly as above.

---

## Verify
```bash
# 1) The all-nodes agent: one per node it can schedule onto.
kubectl get ds node-agent          # DESIRED == CURRENT == READY (== schedulable nodes)

# 2) One agent per node, never two on the same node:
kubectl get pods -o wide -l app=node-agent --no-headers | awk '{print $7}' | sort | uniq -c

# 3) The subset agent: count equals labeled-node count:
kubectl get nodes -l agentpool=observed --no-headers | wc -l
kubectl get ds node-agent-subset -o jsonpath='{.status.desiredNumberScheduled}'; echo

# 4) The tolerant agent covers the tainted node:
kubectl get pods -o wide -l app=node-agent-tolerant | grep "$NODE2"
```
yes Success = `node-agent` runs one pod per schedulable node; `node-agent-subset` runs only on labeled nodes; `node-agent-tolerant` runs on **every** node including the tainted one.

---

## Break it - reproduce BOTH node-level blind spots
A node agent fails *silently*: nothing crashes, you just stop seeing a node. Reproduce the two ways it happens.

Blind spot A - a `nodeSelector` that matches nothing.
```bash
kubectl patch ds node-agent-subset --type merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"agentpool":"does-not-exist"}}}}}'
kubectl get ds node-agent-subset
kubectl get pods -o wide -l app=node-agent-subset
```
**Predict (B1): What is `DESIRED`/`CURRENT` now, and how many pods are running? Did anything error, or did the agent quietly cover nothing**?

Blind spot B - a tainted node with no matching toleration.
You already saw this in step 4: `$NODE2` is tainted `dedicated=special:NoSchedule` and the plain `node-agent` does not tolerate it.
```bash
kubectl get pods -o wide -l app=node-agent | grep "$NODE2" || echo "NO node-agent on $NODE2 -> BLIND SPOT"
```
**Observe (B2):** The plain `node-agent` has no pod on `$NODE2`. If this were fluent-bit, that node's logs would never ship - and you'd discover it mid-incident. Which DaemonSet here *does* cover `$NODE2`, and what one field makes the difference?

**Fix both.**
```bash
# A: point the selector back at the real label
kubectl patch ds node-agent-subset --type merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"agentpool":"observed"}}}}}'
kubectl get ds node-agent-subset

# B: the tolerant DaemonSet (step 5) already covers the tainted node - confirm coverage parity:
kubectl get pods -o wide -l app=node-agent-tolerant --no-headers | awk '{print $7}' | sort | uniq | wc -l
kubectl get nodes --no-headers | wc -l   # should match
```
**Prove it (B3):** After the fix, `node-agent-subset` is back on the labeled node(s), and `node-agent-tolerant` covers as many distinct nodes as the cluster has. No blind spots.

---

## Cleanup
```bash
# Remove the node label and taint we added (these outlive the namespace!):
NODE1=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
NODE2=$(kubectl get nodes -o jsonpath='{.items[1].metadata.name}')
kubectl label node "$NODE1" agentpool-                 # trailing - removes the label
kubectl taint node "$NODE2" dedicated=special:NoSchedule-   # trailing - removes the taint

# Delete the namespace (all DaemonSets/pods go with it):
kubectl delete namespace lab-15-daemonset

# Delete the cluster-scoped PriorityClass (not in the namespace):
kubectl delete priorityclass node-agent-critical
```
No cloud LB/volume was created in this lab. **Double-check** the label and taint are gone - leftover taints make later labs' pods mysteriously `Pending`:
```bash
kubectl get nodes --show-labels | grep -o 'agentpool=[^ ,]*' || echo "label clean"
kubectl describe nodes | grep -A2 Taints | grep dedicated || echo "taint clean"
```

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
