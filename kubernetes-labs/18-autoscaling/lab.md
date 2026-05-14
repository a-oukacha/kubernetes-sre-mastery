# Lab 18 - Autoscaling: HPA, VPA, Cluster Autoscaler · **Exercise**
**Patterns:** Elastic Scale **Source:** KIA 15; KP "Elastic Scale" **Est:** 55 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations and manifests yourself; that *is* the skill. Attempt every task and write down
> your answer to every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done,
> [`solution.md`](solution.md) has the exact commands + the output you should have seen + every
> checkpoint answer. Then read [`lecture.md`](lecture.md) for the course.

## Objective
Scale **pods to load** and **nodes to pods**, *safely*. Drive a CPU-burnable target with `fortio`, watch the **HorizontalPodAutoscaler add replicas as utilization crosses its target and drop them after a stabilization window, push past node capacity so pods go `Pending` and the Cluster Autoscaler / node-pool autoscaler** adds a node, and keep a **PodDisruptionBudget so scale-down never takes you to zero. Then break it two ways - an HPA with no `requests` (`<unknown>`) and a too-twitchy HPA that flaps** - and fix both.

## Concepts exercised
- HPA **v2** (`autoscaling/v2`): `Utilization` target on CPU %, `minReplicas`/`maxReplicas`, the reconcile loop
- the HPA control loop: `desiredReplicas = ceil(currentReplicas × currentMetric / targetMetric)`, polled every ~15s (REACTIVE, with lag)
- CPU % is **relative to `requests`** - no request, no percentage
- HPA `behavior` (`scaleUp`/`scaleDown` policies + `stabilizationWindowSeconds`) to damp flapping
- **VPA** (recommend vs auto modes; why it CONFLICTS with HPA on the same resource)
- **Cluster Autoscaler / Karpenter** (node scale-out when pods are `Pending`) and node-pool autoscaler on OVH
- **PDB** so scale-down/drain stays inside an availability floor
- custom/external metrics + **scale-to-zero** (KEDA/Knative) - conceptual

## Prerequisites
- Labs **02 (Deployments, `fortio` load loop) and 05** (requests/limits - the basis of HPA %) done.
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`).
- **metrics-server installed** - HPA on CPU has no data without it. Confirm with `kubectl top nodes` (returns CPU/MEM, not an error). See `../00-cluster-setup/tooling.md` and `eks.md §2.3`.
- For the node-scale-out step: a cluster whose node pool can grow - **Cluster Autoscaler** (EKS, `eks.md §2.7`) or **node-pool autoscaler** (OVH, `ovh.md §2.5`). If yours can't, read the step and skip the apply.

## Setup
Create a namespace **`lab-18-autoscaling` and make it the default for the rest of this lab. Then confirm metrics-server is live before you go any further - `kubectl top nodes` must return real CPU/MEM numbers, not an error; if it errors, install metrics-server first. No LoadBalancer or volume is created in this lab - load runs in-cluster** over a `ClusterIP` Service.

**Predict (0): You're about to deploy `podinfo` with `replicas: 1` and an HPA with `minReplicas: 1, maxReplicas: 10`. With no load**, how many replicas will the HPA settle the Deployment at?

---

## Steps

### 1. Deploy the target (with requests) + its HPA + a PDB
Apply the three provided manifests `manifests/deploy-target.yaml`, `manifests/hpa.yaml`, and `manifests/pdb.yaml`, wait for the `podinfo` Deployment to roll out, and confirm the `podinfo` HPA exists.

**Predict (1):** Open `deploy-target.yaml` - the pod requests `200m` CPU. The HPA targets `50%`. In CPU terms, how many millicores of average use per pod is the HPA aiming to hold? Check the HPA once metrics warm up (~30-60s).

### 2. Read the HPA before any load - current vs target
Inspect the `podinfo` HPA at rest: read the `TARGETS` column (current% / target%) and the `Metrics:` block from its describe output.

**Observe (2):** Right after apply the `TARGETS` column may read `<unknown>/50%` for ~30s, then settle to something like `0%/50%` (or `1%/50%`) with `REPLICAS 1`. Why `<unknown>` at first, and what made it become a number?

### 3. Turn on load -> CPU climbs above target
Use two terminals. In terminal A, leave a watch running on the `podinfo` HPA. In terminal B, apply `manifests/loadgen.yaml`, wait for it to roll out, then sample per-pod CPU for the `podinfo` pods a few times - CPU should rise toward and past `200m`.

**Predict (3):** `loadgen` hammers `POST /token` (a JWT signing = real CPU work) flat out. As current CPU% crosses 50%, what does the HPA do to `REPLICAS`, and roughly how fast (the `behavior.scaleUp` window is 0s + up to 4 pods / 15s)?

### 4. Watch the pods actually appear
Watch the `podinfo` pods get created until `REPLICAS` stops climbing, then re-read the HPA.

**Observe (4):** As replicas climb, per-pod CPU% should fall back toward 50% (the same load spread over more pods). What replica count does it stabilize at, and does the `TARGETS` number land near `50%`?

### 5. Cut the load -> after the stabilization window, scale down
Stop the load by removing the `loadgen` Deployment, then watch the `podinfo` HPA - the drop is **not** immediate.

**Predict (5):** The HPA `behavior.scaleDown.stabilizationWindowSeconds` is `120`. After load stops, how long before `REPLICAS` starts falling, and what's the smallest it can go (think `minReplicas`)?

**Prove it (5):** Capture the scaling decisions the controller actually made - pull the `Events:` block from the `podinfo` HPA's describe output. You should see `SuccessfulRescale` events with reasons like *"cpu resource utilization above target"* (up) and later *"All metrics below target"* (down). Convince yourself the down event lagged the load-off by ~2 min, not instantly.

### 6. (Optional) VPA in recommend mode - right-sizing, without touching pods
> Skip if the VPA controllers aren't installed (they're not in core Kubernetes / most managed clusters). This is read-only.

If VPA is available, apply `manifests/vpa.yaml`, give it ~60s, then read the `Recommendation` block from the VPA's describe output. If the VPA CRD is missing, read the manifest comments and the lecture's VPA section instead.

**Observe (6):** If VPA is present, `updateMode: "Off"` makes it *recommend* requests/limits in `status` without mutating pods. Note: this VPA recommends on **CPU** - the **same** resource the HPA scales on. Why would flipping it to `Auto` be dangerous here?

### 7. Push past node capacity -> pods go `Pending` -> a node is added
> Needs a growable node pool (CA on EKS / node-pool autoscaler on OVH). Tune the request in `node-pressure.yaml` to your node size so it actually overflows.

In terminal A, leave a watch running on the nodes. In terminal B, apply `manifests/node-pressure.yaml`, then confirm the split: some `ballast` pods Running, some Pending. Filter to just the `Pending` pods, and dig out the scheduling/scale-up reason from the pods' events (look for insufficient-resource, `FailedScheduling`, and `TriggeredScaleUp`).

**Predict (7):** `ballast` asks for 8 pods × `1` CPU. They won't all fit. What `STATUS` do the unschedulable pods show, what event names the reason, and what does the autoscaler do in response over the next 1-4 minutes (watch terminal A for a NEW node)?

#### EKS - Cluster Autoscaler + Karpenter
On EKS the growable capacity comes from either the **Cluster Autoscaler (scales a managed node group between min/max in response to `Pending` pods) or Karpenter** (provisions right-sized nodes directly from `Pending` pod requirements). Confirm which one your cluster runs and that its min/max bounds leave room to add a node, then observe it react to the `ballast` pressure (see `eks.md §2.7`). Prove a new node joins while pods are `Pending`.

#### OVH - node-pool autoscaler
On OVH Managed Kubernetes the equivalent is the **node-pool autoscaler**: enable autoscaling on the pool with a max above your current node count so it can grow under `Pending` pressure, then observe it add a node and absorb the `ballast` pods (see `ovh.md §2.5`). Prove a new node joins while pods are `Pending`.

---

## Verify
Demonstrate success with observable signals: the `podinfo` HPA's `Events:` show `SuccessfulRescale` events **both** up and down; the HPA is back near 1 replica at rest; the HPA reports a real `averageUtilization` in its status (not `<unknown>`); a NEW node appeared under pressure (one more than you started with) and the `Pending` `ballast` pods drained as it joined; and the `podinfo` PDB reports an `ALLOWED DISRUPTIONS` consistent with `minAvailable=1`.

yes Success = `describe hpa` shows scale-up **and** scale-down `SuccessfulRescale` events, the HPA reports a real `averageUtilization` (not `<unknown>`), a new node joined while `ballast` had `Pending` pods, and the PDB keeps at least 1 podinfo pod available.

---

## Break it - the two classic HPA failures

### B1 - HPA with NO requests -> `<unknown>`, never scales
Apply `manifests/break-no-requests.yaml`, wait ~45s, then inspect the `norequests` HPA's `TARGETS` and the `Conditions:` block of its describe output.

**Predict (B1):** The `norequests` Deployment has **no** `resources.requests.cpu`. Even under load, what does the HPA's `TARGETS` show, will `REPLICAS` ever move, and what `Condition`/`Reason` explains it?

**Prove it (B1):** The fix is to give the target a request - that's the whole point. List the HPAs side by side and convince yourself the healthy `podinfo` HPA reports a real percentage while `norequests` reports `<unknown>` purely because there's no request to divide by. Then remove the `break-no-requests` manifest.

### B2 - target too low + no stabilization -> flapping (oscillation)
Swap the good HPA for a twitchy one **on the same `podinfo` Deployment** by applying `manifests/break-flap.yaml` (same name, so it replaces the good HPA - target `10%`, no `behavior`). Re-apply `manifests/loadgen.yaml` to pulse load on, and watch the `podinfo` HPA's `REPLICAS` sawtooth. After ~2-3 minutes of watching, pulse the load off and on a couple of times by scaling the `loadgen` Deployment to 0 and back to 1.

**Predict (B2):** With the target at `10%` and **no** scale-down stabilization window, what shape does `REPLICAS` trace as metrics lag the real load - and why does a `50%` target plus a 120s window (the original `hpa.yaml`) NOT do this?

**Prove it (B2) - fix it:** put the sane HPA back by re-applying `manifests/hpa.yaml` (target 50% + behavior windows) and confirm `REPLICAS` now settles instead of oscillating.

---

## Cleanup
Delete everything under `manifests/`, then delete the `lab-18-autoscaling` namespace. No cloud LB/volume was created. If the node-scale-out step added a node, the Cluster Autoscaler / node-pool autoscaler reclaims it a few minutes after the `ballast` pods are gone (scale-down is intentionally slow - see the lecture).

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
