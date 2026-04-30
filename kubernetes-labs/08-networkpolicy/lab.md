# Lab 08 - NetworkPolicy & segmentation · **Exercise**
**Patterns:** Network Segmentation **Source:** KIA 13; KP "Network Segmentation" **Est:** 50 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations and the `NetworkPolicy` manifests yourself; that *is* the skill. Attempt every
> task and write down your answer to every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or
> done, [`solution.md`](solution.md) has the exact commands + the output you should have seen + every
> checkpoint answer. Then read [`lecture.md`](lecture.md) for the course.

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
- A reachable cluster, 2-3 `Ready` nodes.

## Setup
Create a namespace **`lab-08-networkpolicy`** and make it your default for the rest of the lab. Deploy the three tiers from `manifests/frontend.yaml`, `manifests/backend.yaml`, and `manifests/db.yaml`, wait for all deployments to be available, and list the pods with their `tier` label shown. Capture the frontend, backend, and db pod names into shell variables - you'll exec into all three throughout the lab.

**Predict (0): You created three tiers but applied zero** NetworkPolicies. Can the `frontend` pod reach the `db` Service right now? Should it be able to, in production?

---

## Tasks

### 1. FIRST: is my CNI actually enforcing policy? (do not skip)
A NetworkPolicy is just an object in etcd; **the CNI is what makes it bite.** Before trusting anything else in this lab, prove your CNI actually enforces policy. Establish a `backend->db` baseline that works, then apply `manifests/00-enforcement-probe.yaml` (a deny-all-ingress-to-db policy), give it a moment, and re-test the same hop.

**Prove it (1): With the probe applied, `backend->db` must stop working. If it still succeeds, your CNI is ignoring NetworkPolicy** - every policy below will be a silent no-op. Stop and enable enforcement (`### EKS` / `### OVH`) before continuing. Once the probe demonstrably bites, remove it so it doesn't interfere with the rest of the lab.

### 2. No policy = flat allow-all network (prove every tier reaches every tier)
With the probe gone and no policies in place, test connectivity along all three relevant hops: `frontend->backend`, `frontend->db`, and `backend->db`. Inspect which ones succeed.

**Observe (2): Which hops succeed with zero policies applied? Pay attention to `frontend->db`** specifically - is it a hop that should exist in a properly segmented three-tier app? This is the baseline you're about to remove.

### 3. Apply default-deny ingress -> everything breaks
Apply `manifests/10-default-deny-ingress.yaml`, give it a moment, then re-test `frontend->backend` and `backend->db` and inspect what happens.

**Predict (3): This policy has `podSelector: {}` and `policyTypes: [Ingress]` with no rules**. Which hops still work? (Hint: what does an empty `podSelector` select, and what does "no ingress rules" mean for a selected pod?)

### 4. Add the allow-list, one hop at a time
Apply `manifests/20-allow-frontend-to-backend.yaml` and `manifests/21-allow-backend-to-db.yaml`, then re-test all three hops: the two you just allowed, plus the forbidden `frontend->db`.

**Prove it (4): Demonstrate that `frontend->backend` and `backend->db` come back while `frontend->db` stays blocked - there is no allow rule for it. Convince yourself why a missing allow blocks the hop even though no rule explicitly "denies" it. You have achieved least connectivity**: the data tier is reachable only via the backend.

### 5. Default-deny egress -> DNS breaks (the trap)
So far you've only controlled *ingress*. Now lock down *outbound* too: apply `manifests/30-default-deny-egress.yaml`, give it a moment, and re-test `frontend->backend` - a hop that was working a moment ago.

**Predict (5):** `frontend->backend` had a working ingress allow *and* worked a moment ago. Will it still work now that egress is default-denied? Why or why not - what does reaching `backend` by name require the client to do *before* it ever opens a TCP connection?

### 6. Allow DNS egress + the intended hop -> fixed
Apply `manifests/31-allow-dns-egress.yaml` (DNS to kube-dns) and `manifests/32-allow-tier-egress.yaml` (the intended tier-to-tier hops), then re-test `frontend->backend` and `backend->db`.

**Observe (6):** Both intended hops work again. What was the precise change that brought name resolution back? Note that nothing here grants egress to the whole internet - confirm the pods now have *exactly* the connectivity they need and no more.

---

## Verify
Demonstrate success with observable signals: `frontend->backend` succeeds, `backend->db` succeeds, **`frontend->db` is blocked**, and DNS works for the allowed hops. List the NetworkPolicies in the namespace to confirm what's enforcing this. Then prove egress containment by showing that a pod cannot reach an arbitrary internet address.

yes Success = `frontend->backend` ok, `backend->db` ok, **`frontend->db` blocked**, DNS works for the allowed hops, and the internet is unreachable. That is the three-tier app at least connectivity.

---

## Break it - the "I added a NetworkPolicy and DNS broke" outage
Reproduce the single most common NetworkPolicy incident. Tear the namespace back to *ingress-only* policies by deleting the two egress-allow manifests (`31-allow-dns-egress.yaml` and `32-allow-tier-egress.yaml`) while default-deny egress remains in force, then re-test `frontend->backend`.

**Predict (B1): The ingress allow `frontend->backend` is still in place**. So why might reaching `backend` now fail - and will the error look like a *network* timeout or a *name resolution* failure? Run it and read the exact message.

Now prove the failure mode is DNS and not the hop itself: attempt a name lookup of `backend` from the frontend pod, and separately attempt to reach the backend by its fully-qualified Service DNS name. Compare the two outcomes.

**Observe (B2): Why does name resolution fail while the ingress allow is irrelevant? Tie this back to what default-deny egress did to traffic toward kube-dns. This is the outage:** segment egress, forget DNS, and every Service name goes dark for the selected pods.

Now apply the fix (re-apply the DNS-egress and tier-egress manifests) and re-test `frontend->backend`.

**Prove it (B3):** Traffic returns. Identify the single rule that was the actual fix.

> **The other, scarier failure:** if your CNI does **not** enforce policy (Step 1), *none* of this Break-it bites - every hop "just works," including `frontend->db`. A passing app with non-enforcing policies is a **false sense of security**, not a secure cluster. Re-run Step 1's probe any time you doubt enforcement.

---

### EKS
EKS's default **Amazon VPC CNI** does **not** enforce NetworkPolicy out of the box - without action, Step 1's probe will NOT bite. You have two options. First, enable VPC CNI network policy on the `vpc-cni` add-on (recommended on recent EKS) so its eBPF agent enforces standard `networking.k8s.io/v1` policies - either by updating the add-on's configuration to set `enableNetworkPolicy=true`, or by setting that field at cluster-create time (see `00-cluster-setup/eks.md §2.9`); then wait for the `aws-node` DaemonSet to roll out. Alternatively, install the Calico policy engine (Tigera operator) alongside the VPC CNI; it enforces the same `NetworkPolicy` objects (see `00-cluster-setup/eks.md §2.9`).

Re-run **Step 1** after enabling - the probe must make `backend->db` stop working before you trust the rest of the lab.

### OVH
OVH Managed Kubernetes (MKS) ships **Cilium on recent versions (older clusters: Canal = Flannel + Calico). Both enforce standard `NetworkPolicy`, so Step 1's probe usually bites with no extra setup - but verify, don't assume: check which CNI is running in `kube-system`. If Step 1's probe does not** bite on your version, install Calico/Cilium per `00-cluster-setup/ovh.md §2.6`. (Cilium additionally unlocks L7/identity policy - the mesh-adjacent territory the lecture contrasts; not used in this lab.)

---

## Cleanup
Delete the `lab-08-networkpolicy` namespace. No cloud LB/volume was created in this lab - deleting the namespace removes the pods, Services, and all NetworkPolicies. (If you enabled the VPC CNI add-on / installed Calico/Cilium for enforcement, that is a **cluster-level** add-on shared by later labs - leave it.)

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
