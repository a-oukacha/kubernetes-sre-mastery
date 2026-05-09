# Lab 14 - Scheduling: affinity, anti-affinity, topology spread, taints/tolerations · **Solution**
**Patterns:** Automated Placement **Source:** KIA 16; KP "Automated Placement" **Est:** 55 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
- A reachable **multi-node** cluster (`kubectl get nodes` -> **2-3 `Ready`**, ideally across multiple AZs - the course cluster is). See `../00-cluster-setup/`.
- Permission to **label and taint nodes** (`kubectl auth can-i patch nodes` -> yes).

## Setup
```bash
kubectl create namespace lab-14-scheduling
kubens lab-14-scheduling             # or add -n lab-14-scheduling to every command

# Capture node names and their zone labels - you'll reference these throughout:
kubectl get nodes -o custom-columns='NODE:.metadata.name,ZONE:.metadata.labels.topology\.kubernetes\.io/zone'
```
**Predict (0): Run `kubectl get nodes --show-labels | tr ',' '\n' | grep -i zone`. Does every** node already carry a `topology.kubernetes.io/zone` label without you doing anything? (Who set it?)

Pick one node to be your "fast disk" node and export both for convenience:
```bash
export N1=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
export N2=$(kubectl get nodes -o jsonpath='{.items[1].metadata.name}')
echo "N1=$N1  N2=$N2"
```

---

## Steps

### 1. Label a node, then pin pods to it with nodeAffinity (HARD)
```bash
kubectl label node "$N1" course.disktype=ssd
kubectl get nodes -L course.disktype

kubectl apply -f manifests/deploy-nodeaffinity.yaml
kubectl get pods -l app.kubernetes.io/name=pinned -o wide
```
**Predict (1):** `deploy-nodeaffinity.yaml` requires `course.disktype=ssd` (`requiredDuringScheduling...`). All 3 replicas - which node(s) will the `NODE` column show? Could two replicas share one node?

**Prove it (1):** Every `pinned` replica's `NODE` is `$N1` and nothing else:
```bash
kubectl get pods -l app.kubernetes.io/name=pinned -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort | uniq -c
```

### 2. Spread one replica per node with podAntiAffinity (SOFT)
```bash
kubectl apply -f manifests/deploy-antiaffinity.yaml
kubectl get pods -l app.kubernetes.io/name=spread-hostname -o wide
```
**Predict (2):** `deploy-antiaffinity.yaml` uses `preferredDuringScheduling...` anti-affinity with `topologyKey=kubernetes.io/hostname` and 3 replicas. On a 3-node cluster, how many distinct nodes will the replicas occupy? On a 2-node cluster, what happens to the 3rd replica - Pending, or does it double up?

**Prove it (2):** Count distinct nodes used:
```bash
kubectl get pods -l app.kubernetes.io/name=spread-hostname \
  -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort | uniq -c
```

### 3. Balance replicas across AZs with topologySpreadConstraints
```bash
kubectl apply -f manifests/deploy-topologyspread.yaml
kubectl get pods -l app.kubernetes.io/name=spread-zone -o wide
```
**Predict (3):** `deploy-topologyspread.yaml` has 4 replicas, `maxSkew=1`, `topologyKey=topology.kubernetes.io/zone`. On a 3-AZ cluster, how will 4 replicas distribute across the 3 zones? (How even can it be with `maxSkew=1`?)

**Prove it (3):** Join each pod to its node's zone label and count per zone:
```bash
for p in $(kubectl get pods -l app.kubernetes.io/name=spread-zone -o jsonpath='{.items[*].metadata.name}'); do
  node=$(kubectl get pod "$p" -o jsonpath='{.spec.nodeName}')
  zone=$(kubectl get node "$node" -o jsonpath='{.metadata.labels.topology\.kubernetes\.io/zone}')
  echo "$zone"
done | sort | uniq -c
```
The per-zone counts should differ by **at most 1** (e.g. `2 / 1 / 1` across three zones).

### 4. Taint a node - an untolerated pod AVOIDS it
```bash
# Dedicate N2: repel everything that doesn't explicitly tolerate it.
kubectl taint node "$N2" course.dedicated=gpu:NoSchedule
# Also label it so an affinity rule can target it by name (step 5 pins onto it):
kubectl label node "$N2" course.dedicated=gpu
kubectl describe node "$N2" | sed -n '/Taints:/p'

kubectl apply -f manifests/pod-no-toleration.yaml
kubectl get pod no-toleration -o wide
```
**Predict (4):** `no-toleration` has **no** toleration. Can it land on `$N2`? Which node will the `NODE` column show?

**Prove it (4):** Its node is never `$N2`:
```bash
kubectl get pod no-toleration -o jsonpath='{.spec.nodeName}{"\n"}'   # -> NOT $N2
```

### 5. Add the matching toleration - now the pod CAN land on the tainted node
```bash
kubectl apply -f manifests/pod-toleration.yaml
kubectl get pod tolerated -o wide
```
**Predict (5): `tolerated` carries the toleration for `course.dedicated=gpu:NoSchedule` and** a `nodeAffinity` requiring `course.dedicated=gpu` (the label you added in step 4). Where does it land, and why both pieces?

**Prove it (5):** It scheduled onto the tainted, dedicated node:
```bash
kubectl get pod tolerated -o jsonpath='{.spec.nodeName}{"\n"}'   # -> $N2
```

---

## Verify
```bash
# (1) nodeAffinity: all 'pinned' replicas on the labeled node:
kubectl get pods -l app.kubernetes.io/name=pinned \
  -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort -u      # -> only $N1

# (2) podAntiAffinity: one replica per node (distinct nodes):
kubectl get pods -l app.kubernetes.io/name=spread-hostname \
  -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort | uniq -c

# (3) topology spread: balanced per zone, skew <= 1:
for p in $(kubectl get pods -l app.kubernetes.io/name=spread-zone -o jsonpath='{.items[*].metadata.name}'); do
  kubectl get node "$(kubectl get pod "$p" -o jsonpath='{.spec.nodeName}')" \
    -o jsonpath='{.metadata.labels.topology\.kubernetes\.io/zone}{"\n"}'
done | sort | uniq -c

# (4)+(5) taint/toleration: untolerated avoids the node, tolerated lands on it:
kubectl get pod no-toleration tolerated -o wide
```
yes Success = `pinned` only on `$N1`; `spread-hostname` one-per-node; `spread-zone` differs by at most 1 per zone; `no-toleration` is **not** on `$N2` while `tolerated` **is**.

---

## Break it - over-constrain scheduling into permanent Pending
A HARD one-per-node anti-affinity with more replicas than nodes is unsatisfiable: the surplus replicas have no eligible node and wedge `Pending` forever.
```bash
kubectl apply -f manifests/deploy-overconstrained.yaml
kubectl get pods -l app.kubernetes.io/name=overconstrained -o wide       # some Running, the rest Pending
```
**Predict (B1): `deploy-overconstrained.yaml` asks for 9** replicas with `requiredDuringScheduling` anti-affinity (`topologyKey=hostname`). On a 3-node cluster, how many become `Running` and how many stay `Pending`? Will the Pending ones ever schedule on their own?

Debug it the right way - events from the scheduler, not logs:
```bash
PENDING=$(kubectl get pods -l app.kubernetes.io/name=overconstrained \
  --field-selector=status.phase=Pending -o jsonpath='{.items[0].metadata.name}')
kubectl describe pod "$PENDING" | sed -n '/Events:/,$p'
```
**Observe (B2):** Read the `FailedScheduling` message. Which phrase names the cause - something about "didn't match pod anti-affinity rules" / "node(s) didn't satisfy"? How many nodes did the scheduler report it filtered out, and why?

**Prove it (B3): Relax the constraint and watch the Pending pods recover. Either reduce replicas to the node count, or switch the rule to soft**:
```bash
# Option A - fewer replicas than nodes:
kubectl scale deployment overconstrained --replicas=3
kubectl get pods -l app.kubernetes.io/name=overconstrained -o wide       # all Running now

# Option B (alternative) - edit the manifest's anti-affinity from
#   requiredDuringSchedulingIgnoredDuringExecution
# to preferredDuringSchedulingIgnoredDuringExecution, then re-apply at 9 replicas:
# the surplus replicas double up instead of pending.
```
Convince yourself the formerly-Pending pods are now `Running` - nothing about the cluster changed except the **constraint you relaxed**.

---

## Cleanup
```bash
kubectl delete namespace lab-14-scheduling

# IMPORTANT: undo every NODE mutation you made (these are cluster-scoped, the
# namespace delete does NOT remove them). Trailing '-' removes a label/taint.
kubectl label node "$N1" course.disktype-
kubectl label node "$N2" course.dedicated-
kubectl taint node "$N2" course.dedicated=gpu:NoSchedule-

# Verify nothing of ours remains on the nodes:
kubectl get nodes -L course.disktype -L course.dedicated
kubectl describe node "$N2" | sed -n '/Taints:/p'        # -> Taints: <none> (or only pre-existing)
```
No node was drained or cordoned in this lab, so there is nothing to `uncordon`. No cloud LB/volume was created - deleting the namespace plus removing the labels/taints fully reverses the lab.

---

### EKS
- **Zone labels are free.** Managed node groups stamp `topology.kubernetes.io/zone` (and `kubernetes.io/hostname`) automatically, so step 3's topology spread works out of the box on a multi-AZ cluster (`eks-cluster.yaml` spans 3 AZs). Confirm with `kubectl get nodes -L topology.kubernetes.io/zone`.
- Dedicated pools = a tainted managed node group. Instead of `kubectl taint` by hand, bake it into the group so new/replacement nodes are born tainted:
  ```yaml
  # eksctl managedNodeGroups[] entry
  - name: dedicated
    labels: { course.dedicated: gpu }
    taints:
      - key: course.dedicated
        value: gpu
        effect: NoSchedule
  ```
 Real GPU groups taint `nvidia.com/gpu:NoSchedule` so only GPU workloads (with the toleration) land there - see the AI/ML notes in the lecture.

### OVH
- **Node pools carry zone labels too.** A multi-AZ MKS pool spreads its nodes across the region's zones and labels them `topology.kubernetes.io/zone`; step 3 works unchanged. Verify with `kubectl get nodes -L topology.kubernetes.io/zone`.
- Label & taint at the pool, not the node. OVH pools support `labels` and `taints` (Manager UI, or Terraform `ovh_cloud_project_kube_nodepool` `template.metadata.labels` / `template.spec.taints`). Set `course.dedicated=gpu` + the `NoSchedule` taint on a dedicated pool so the pool's autoscaler reproduces them on every new node - hand-tainting a single node (as this lab does for speed) is lost when that node is replaced.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
