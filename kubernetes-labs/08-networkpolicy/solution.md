# Lab 08 - NetworkPolicy & segmentation · **Solution**
**Patterns:** Network Segmentation **Source:** KIA 13; KP "Network Segmentation" **Est:** 50 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

## Objective
Take a three-tier app on Kubernetes' **default flat, allow-all network** and squeeze it down to **least connectivity**: walk the progression default-allow -> default-deny -> additive allow-list, prove that `frontend->db` is blocked while `frontend->backend->db` still flows, and survive the classic "I added a NetworkPolicy and DNS broke" outage.

## Concepts exercised
- the default flat network (every pod can reach every pod, across namespaces)
- `NetworkPolicy`: `podSelector`, `ingress`/`egress` rules, `namespaceSelector`, `ports`
- default-deny (ingress and egress) and the **additive allow-list** model (union; there is no "deny" rule)
- **CNI dependence** - a policy is inert unless the CNI enforces it
- egress control: allowing DNS to kube-dns while blocking the internet
- L3/L4 (NetworkPolicy) vs L7/mTLS (service mesh) - conceptual contrast

## Prerequisites
- Labs 01 (kubectl/labels) and 06 (Services & cluster DNS) done, or equivalent fluency.
- **A CNI that enforces NetworkPolicy.** This is not optional and not universal - Step 1 verifies it. If yours doesn't, see `### EKS` / `### OVH` below to enable Calico/Cilium.
- `kubectl get nodes` -> 2-3 `Ready`.

## Setup
```bash
kubectl create namespace lab-08-networkpolicy
kubens lab-08-networkpolicy          # or add -n lab-08-networkpolicy to every command

kubectl apply -f manifests/frontend.yaml
kubectl apply -f manifests/backend.yaml
kubectl apply -f manifests/db.yaml
kubectl wait --for=condition=available deploy --all --timeout=60s
kubectl get pods -L tier
```
Grab the pod names once - you'll exec into them all lab:
```bash
FE=$(kubectl get pod -l tier=frontend -o jsonpath='{.items[0].metadata.name}')
BE=$(kubectl get pod -l tier=backend  -o jsonpath='{.items[0].metadata.name}')
DB=$(kubectl get pod -l tier=db       -o jsonpath='{.items[0].metadata.name}')
echo "$FE / $BE / $DB"
```
**Predict (0): You created three tiers but applied zero** NetworkPolicies. Can the `frontend` pod reach the `db` Service right now? Should it be able to, in production?

---

## Steps

### 1. FIRST: is my CNI actually enforcing policy? (do not skip)
A NetworkPolicy is just an object in etcd; **the CNI is what makes it bite.** Prove enforcement with a tiny deny-everything-to-db policy, *then* delete it:
```bash
kubectl exec "$BE" -- wget -qO- --timeout=3 db:8080/hostname; echo   # baseline: works
kubectl apply -f manifests/00-enforcement-probe.yaml                 # deny ALL ingress to db
sleep 3
kubectl exec "$BE" -- wget -qO- --timeout=3 db:8080/hostname; echo   # should now TIME OUT
```
**Prove it (1): With the probe applied, `backend->db` must time out (`wget: download timed out`). If it still returns a hostname, your CNI is ignoring NetworkPolicy** - every policy below will be a silent no-op. Stop and enable enforcement (`### EKS` / `### OVH`) before continuing. When the probe bites, remove it:
```bash
kubectl delete -f manifests/00-enforcement-probe.yaml
```

### 2. No policy = flat allow-all network (prove every tier reaches every tier)
```bash
kubectl exec "$FE" -- wget -qO- --timeout=3 backend:8080/hostname; echo
kubectl exec "$FE" -- wget -qO- --timeout=3 db:8080/hostname; echo      # frontend -> db !
kubectl exec "$BE" -- wget -qO- --timeout=3 db:8080/hostname; echo
```
**Observe (2):** All three succeed - including **`frontend->db`**, a hop that should never exist in a segmented app. This is Kubernetes' default: a flat network where any pod can open a connection to any other pod (even across namespaces). Note this is the *baseline you're about to remove*.

### 3. Apply default-deny ingress -> everything breaks
```bash
kubectl apply -f manifests/10-default-deny-ingress.yaml
sleep 3
kubectl exec "$FE" -- wget -qO- --timeout=3 backend:8080/hostname; echo   # ?
kubectl exec "$BE" -- wget -qO- --timeout=3 db:8080/hostname; echo        # ?
```
**Predict (3): This policy has `podSelector: {}` and `policyTypes: [Ingress]` with no rules**. Which hops still work? (Hint: what does an empty `podSelector` select, and what does "no ingress rules" mean for a selected pod?)

### 4. Add the allow-list, one hop at a time
```bash
kubectl apply -f manifests/20-allow-frontend-to-backend.yaml
kubectl apply -f manifests/21-allow-backend-to-db.yaml
sleep 3
kubectl exec "$FE" -- wget -qO- --timeout=3 backend:8080/hostname; echo   # allowed hop
kubectl exec "$BE" -- wget -qO- --timeout=3 db:8080/hostname; echo        # allowed hop
kubectl exec "$FE" -- wget -qO- --timeout=3 db:8080/hostname; echo        # the forbidden hop
```
**Prove it (4): `frontend->backend` and `backend->db` come back, but `frontend->db` still times out** - there is no allow rule for it, and policies are *additive allow-lists* (you cannot have reached it by "not denying" it). You have achieved **least connectivity**: the data tier is reachable only via the backend.

### 5. Default-deny egress -> DNS breaks (the trap)
So far we only controlled *ingress*. Now lock down *outbound* too:
```bash
kubectl apply -f manifests/30-default-deny-egress.yaml
sleep 3
kubectl exec "$FE" -- wget -qO- --timeout=3 backend:8080/hostname; echo   # was allowed... still?
```
**Predict (5):** `frontend->backend` had a working ingress allow *and* worked a moment ago. Will it still work now that egress is default-denied? Why or why not - what does `wget backend` need to do *before* it ever opens a TCP connection?

### 6. Allow DNS egress + the intended hop -> fixed
```bash
kubectl apply -f manifests/31-allow-dns-egress.yaml     # 53/udp + 53/tcp to kube-dns
kubectl apply -f manifests/32-allow-tier-egress.yaml    # frontend->backend, backend->db
sleep 3
kubectl exec "$FE" -- wget -qO- --timeout=3 backend:8080/hostname; echo
kubectl exec "$BE" -- wget -qO- --timeout=3 db:8080/hostname; echo
```
**Observe (6):** Both intended hops work again. DNS resolution returned the instant the `:53`-to-kube-dns egress allow landed. Notice nothing grants egress to `0.0.0.0/0` - the pods now have *exactly* the connectivity they need and no path to the internet.

---

## Verify
```bash
kubectl exec "$FE" -- wget -qO- --timeout=3 backend:8080/hostname; echo   # SUCCEEDS (pod name)
kubectl exec "$BE" -- wget -qO- --timeout=3 db:8080/hostname; echo        # SUCCEEDS (pod name)
kubectl exec "$FE" -- wget    -qO- --timeout=3 db:8080/hostname; echo     # TIMES OUT (blocked)
kubectl get networkpolicy -L course.lab
```
yes Success = `frontend->backend` ok, `backend->db` ok, **`frontend->db` blocked**, and DNS works for the allowed hops. That is the three-tier app at least connectivity.

Confirm the internet is blocked too (egress containment):
```bash
kubectl exec "$BE" -- wget -qO- --timeout=3 https://example.com; echo     # TIMES OUT / fails
```

---

## Break it - the "I added a NetworkPolicy and DNS broke" outage
Reproduce the single most common NetworkPolicy incident. Tear the namespace back to *ingress-only* policies, then add a default-deny **egress** with **no DNS allow**:
```bash
kubectl delete -f manifests/31-allow-dns-egress.yaml -f manifests/32-allow-tier-egress.yaml
sleep 3
kubectl exec "$FE" -- wget -qO- --timeout=3 backend:8080/hostname; echo
```
**Predict (B1): The ingress allow `frontend->backend` is still in place**. So why might `wget backend` now fail - and will the error look like a *network* timeout or a *name resolution* failure? Run it and read the exact message.

Confirm it's DNS, not the hop:
```bash
kubectl exec "$FE" -- nslookup backend 2>&1 | head; echo "---"
kubectl exec "$FE" -- wget -qO- --timeout=3 backend.lab-08-networkpolicy.svc.cluster.local:8080/hostname; echo
```
**Observe (B2):** Name resolution fails (lookup timeout / no answer) because default-deny egress also blocked `:53` to kube-dns. The ingress allow is irrelevant - the client can't even *find* the address. **This is the outage:** segment egress, forget DNS, and every Service name goes dark for the selected pods.

Now apply the fix and watch it recover:
```bash
kubectl apply -f manifests/31-allow-dns-egress.yaml
kubectl apply -f manifests/32-allow-tier-egress.yaml
sleep 3
kubectl exec "$FE" -- wget -qO- --timeout=3 backend:8080/hostname; echo   # recovered
```
**Prove it (B3):** Traffic returns. The fix was one rule: egress to kube-dns on `53/udp`+`53/tcp`.

> **The other, scarier failure:** if your CNI does **not** enforce policy (Step 1), *none* of this Break-it bites - every hop "just works," including `frontend->db`. A passing app with non-enforcing policies is a **false sense of security**, not a secure cluster. Re-run Step 1's probe any time you doubt enforcement.

---

### EKS
EKS's default **Amazon VPC CNI** does **not** enforce NetworkPolicy out of the box - without action, Step 1's probe will NOT bite. Two options:
- VPC CNI network policy (recommended on recent EKS): enable it on the add-on so the VPC CNI's eBPF agent enforces standard `networking.k8s.io/v1` policies:
  ```bash
  aws eks update-addon --cluster-name "$CLUSTER" --addon-name vpc-cni \
    --configuration-values '{"enableNetworkPolicy":"true"}'
  # or set enableNetworkPolicy=true on the vpc-cni add-on at create time (see 00-cluster-setup/eks.md §2.9)
  kubectl -n kube-system rollout status ds/aws-node
  ```
- **Calico:** install the Calico policy engine (Tigera operator) alongside the VPC CNI; it enforces the same `NetworkPolicy` objects. See `00-cluster-setup/eks.md §2.9`.

Re-run **Step 1** after enabling - the probe must make `backend->db` time out before you trust the rest of the lab.

### OVH
OVH Managed Kubernetes (MKS) ships **Cilium on recent versions (older clusters: Canal = Flannel + Calico). Both enforce standard `NetworkPolicy`, so Step 1's probe usually bites with no extra setup - but verify, don't assume**:
```bash
kubectl get pods -n kube-system | grep -Ei 'cilium|canal|calico'   # which CNI am I on?
```
If Step 1's probe does **not** bite on your version, install Calico/Cilium per `00-cluster-setup/ovh.md §2.6`. (Cilium additionally unlocks L7/identity policy - the mesh-adjacent territory the lecture contrasts; not used in this lab.)

---

## Cleanup
```bash
kubectl delete namespace lab-08-networkpolicy
```
No cloud LB/volume was created in this lab - deleting the namespace removes the pods, Services, and all NetworkPolicies. (If you enabled the VPC CNI add-on / installed Calico/Cilium for enforcement, that is a **cluster-level** add-on shared by later labs - leave it.)

---
*Done? Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
