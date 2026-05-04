# Lab 11 - Singleton, PodDisruptionBudget & leader election · **Solution**
**Patterns: Singleton Service + Ownership Election Source: KP "Singleton Service"; DDS "Ownership Election"; KIA 4 Est:** 55 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
```bash
kubectl create namespace lab-11-singleton-pdb-leader
kubens lab-11-singleton-pdb-leader      # or add -n lab-11-singleton-pdb-leader to every command
```
No LoadBalancer or volume is created in this lab - everything runs in-cluster and CPU-only. The only resource that escapes the namespace is a **cordoned node** (you'll cordon one during the drain demo); Cleanup `uncordon`s it.

---

## Steps

### 1. Deploy the three leader candidates and their RBAC
```bash
kubectl apply -f manifests/leader-rbac.yaml
kubectl apply -f manifests/leader-deploy.yaml
kubectl rollout status deployment/leader-elector    # 3 pods Ready
kubectl get pods -l app.kubernetes.io/name=leader-elector -o wide
```
**Predict (1):** Three identical pods all run the *same* contention script against the *same* Lease object. How many of them will end up claiming "I AM THE LEADER" at steady state? Why can't it be all three?

### 2. Watch exactly one pod win the lease
```bash
# tail all three candidates at once; one line stream:
kubectl logs -l app.kubernetes.io/name=leader-elector --prefix --tail=5 -f
#   (Ctrl-C after you see steady state - ~20s. Or use: stern leader-elector)
```
**Observe (2): Across the three pods, how many print `I AM THE LEADER`** each cycle, and what do the other two print? Note the leader pod's name.

### 3. Read the Lease object - the source of truth
```bash
kubectl get lease demo-singleton
kubectl get lease demo-singleton -o yaml | sed -n '/spec:/,$p'
kubectl get lease demo-singleton -o jsonpath='{.spec.holderIdentity}'; echo
```
**Prove it (3): The `holderIdentity` in the Lease equals exactly one** pod name - the same pod that logs leadership in step 2. The leadership claim and the Lease agree. Capture that pod name; you're about to kill it.

### 4. Kill the leader - watch failover happen
```bash
LEADER=$(kubectl get lease demo-singleton -o jsonpath='{.spec.holderIdentity}')
echo "current leader: $LEADER"
kubectl delete pod "$LEADER"        # the leader stops renewing
# now watch the lease holder change (the Deployment also replaces the pod):
kubectl get lease demo-singleton -o jsonpath='{.spec.holderIdentity}' -w
#   (Ctrl-C once holderIdentity changes to a DIFFERENT pod name)
```
**Predict (4): The lease's `leaseDurationSeconds` is 15**. After you delete the leader, roughly how long until a *different* pod's name appears as `holderIdentity`, and why is there a delay at all (rather than instant takeover)?

### 5. Confirm a new leader took over
```bash
kubectl get lease demo-singleton -o jsonpath='{.spec.holderIdentity}'; echo   # a NEW pod name
kubectl logs -l app.kubernetes.io/name=leader-elector --prefix --tail=3 | grep -i leader
```
**Prove it (5):** `holderIdentity` is now a **different pod than the one you killed, and that pod's logs say `I AM THE LEADER`. Failover happened with no human action** - the lease lapsed, a standby grabbed it.

### 6. Deploy the web app + a sane PDB
```bash
kubectl apply -f manifests/web-deploy.yaml
kubectl apply -f manifests/web-pdb.yaml
kubectl rollout status deployment/web        # 3 pods Ready
kubectl get pdb web
kubectl get pods -l app.kubernetes.io/name=web -o wide   # note WHICH nodes they're on
```
**Observe (6):** `kubectl get pdb web` shows `MIN AVAILABLE 2` and `ALLOWED DISRUPTIONS`. With 3 healthy replicas and `minAvailable: 2`, how many `ALLOWED DISRUPTIONS` do you expect, and what does that number mean for a drain?

### 7. Drain a node - the PDB paces the eviction
Pick a node that actually hosts a `web` pod, then drain it:
```bash
NODE=$(kubectl get pods -l app.kubernetes.io/name=web -o jsonpath='{.items[0].spec.nodeName}')
echo "draining: $NODE"
kubectl drain "$NODE" --ignore-daemonsets --delete-emptydir-data --pod-selector app.kubernetes.io/name=web
```
**Predict (7):** `drain` first **cordons** the node, then evicts the web pod on it through the eviction API. Given `minAvailable: 2`, will this single eviction succeed immediately, or wait? What has to become true elsewhere in the cluster before the eviction is allowed?

**Observe (7b):** While the drain runs (or just after), watch the replacement come up:
```bash
kubectl get pods -l app.kubernetes.io/name=web -o wide -w   # Ctrl-C after a new pod is Ready on another node
```
Did total `web` availability ever drop below 2 Ready? (That is the PDB's whole job.)

---

## Verify
```bash
# Exactly one leader, agreed on by the Lease:
kubectl get lease demo-singleton -o jsonpath='{.spec.holderIdentity}'; echo   # one pod name
kubectl logs -l app.kubernetes.io/name=leader-elector --tail=20 | grep -c 'I AM THE LEADER'   # only the holder is logging it

# Drain respected the PDB (web stayed >= 2 Ready throughout):
kubectl get deploy web -o jsonpath='{.status.readyReplicas}/{.status.replicas} ready'; echo    # -> 3/3 ready again
kubectl get pdb web    # ALLOWED DISRUPTIONS back to 1 once all 3 are Ready
```
yes Success = the Lease names exactly one holder; after you killed the leader the holder **changed; and `kubectl drain` evicted the web pod without** ever dropping below 2 Ready replicas.

Now un-cordon the node you drained so it can schedule again (you'll re-drain it in Break-it):
```bash
kubectl uncordon "$NODE"
```

---

## Break it - an over-tight PDB hangs the upgrade forever
Replace the sane PDB with one that protects **100%** of replicas, then try to drain:
```bash
kubectl apply -f manifests/web-pdb-overtight.yaml     # minAvailable: 100% (same name -> replaces web pdb)
kubectl get pdb web                                   # ALLOWED DISRUPTIONS -> 0
NODE=$(kubectl get pods -l app.kubernetes.io/name=web -o jsonpath='{.items[0].spec.nodeName}')
kubectl drain "$NODE" --ignore-daemonsets --delete-emptydir-data --pod-selector app.kubernetes.io/name=web
#   ^ this will NOT finish - watch the messages, then Ctrl-C after ~30s
```
**Predict (B1):** With `minAvailable: 100%`, what is `ALLOWED DISRUPTIONS`, and what exact message does `drain` repeat? Will it *ever* complete on its own?

**Observe (B2):** The node is now **cordoned but undrained** - the eviction can never satisfy the budget, so `drain` retries forever. In the real world this is a node-group/node-pool rolling upgrade stuck at "draining node 1 of 30." Relax the PDB and watch the drain unblock:
```bash
kubectl apply -f manifests/web-pdb.yaml      # back to minAvailable: 2
kubectl get pdb web                          # ALLOWED DISRUPTIONS -> 1 again
kubectl drain "$NODE" --ignore-daemonsets --delete-emptydir-data --pod-selector app.kubernetes.io/name=web
#   now it completes
```
**Prove it (B3): With the relaxed PDB the same `drain` command completes**. The only thing that changed was the budget - proving the hang was the PDB, not the cluster.

Observe (B4) - replicas=1 is NOT a true singleton. The web app is not a singleton; the leader-elector is. Reason about why a `replicas: 1` Deployment would *still* not give you at-most-once: during a rolling update (or an involuntary reschedule) the new pod can start **before** the old one is fully gone, so two can run briefly. (Predict what guarantees you'd need instead - the lecture covers lease vs StatefulSet ordinal.)

---

## Cleanup
```bash
# IMPORTANT: un-cordon any node you drained, or it stays unschedulable for later labs:
kubectl get nodes        # any SchedulingDisabled?
kubectl uncordon "$NODE" 2>/dev/null || true
# if you lost $NODE, uncordon every cordoned node:
for n in $(kubectl get nodes -o jsonpath='{range .items[?(@.spec.unschedulable==true)]}{.metadata.name}{"\n"}{end}'); do kubectl uncordon "$n"; done

kubectl delete namespace lab-11-singleton-pdb-leader
```
The namespace delete removes the Deployments, Service, Lease, RBAC and PDBs. No cloud LB/volume was created. Double-check no node is left `SchedulingDisabled` - a forgotten cordon silently shrinks every later lab's capacity.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
