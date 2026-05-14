# Lab 18 - Autoscaling: HPA, VPA, Cluster Autoscaler · **Solution**
**Patterns:** Elastic Scale **Source:** KIA 15; KP "Elastic Scale" **Est:** 55 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
```bash
kubectl create namespace lab-18-autoscaling
kubens lab-18-autoscaling          # or add -n lab-18-autoscaling to every command
kubectl top nodes                  # MUST return numbers; if it errors, install metrics-server first
```
No LoadBalancer or volume is created in this lab - load runs **in-cluster** over a `ClusterIP` Service.

**Predict (0): You're about to deploy `podinfo` with `replicas: 1` and an HPA with `minReplicas: 1, maxReplicas: 10`. With no load**, how many replicas will the HPA settle the Deployment at?

---

## Steps

### 1. Deploy the target (with requests) + its HPA + a PDB
```bash
kubectl apply -f manifests/deploy-target.yaml
kubectl apply -f manifests/hpa.yaml
kubectl apply -f manifests/pdb.yaml
kubectl rollout status deployment/podinfo
kubectl get hpa podinfo
```
**Predict (1):** Open `deploy-target.yaml` - the pod requests `200m` CPU. The HPA targets `50%`. In CPU terms, how many millicores of average use per pod is the HPA aiming to hold? Check `kubectl get hpa podinfo` once metrics warm up (~30-60s).

### 2. Read the HPA before any load - current vs target
```bash
kubectl get hpa podinfo            # TARGETS column = current%/target%
kubectl describe hpa podinfo | sed -n '/Metrics:/,/Events:/p'
```
**Observe (2):** Right after apply the `TARGETS` column may read `<unknown>/50%` for ~30s, then settle to something like `0%/50%` (or `1%/50%`) with `REPLICAS 1`. Why `<unknown>` at first, and what made it become a number?

### 3. Turn on load -> CPU climbs above target
In one terminal start a watch; in another start the load:
```bash
# terminal A - leave running:
kubectl get hpa podinfo -w
```
```bash
# terminal B:
kubectl apply -f manifests/loadgen.yaml
kubectl rollout status deployment/loadgen
kubectl top pods -l app.kubernetes.io/name=podinfo   # re-run a few times; CPU should rise toward/over 200m
```
**Predict (3):** `loadgen` hammers `POST /token` (a JWT signing = real CPU work) flat out. As current CPU% crosses 50%, what does the HPA do to `REPLICAS`, and roughly how fast (the `behavior.scaleUp` window is 0s + up to 4 pods / 15s)?

### 4. Watch the pods actually appear
```bash
kubectl get pods -l app.kubernetes.io/name=podinfo -w   # Ctrl-C once REPLICAS stops climbing
kubectl get hpa podinfo
```
**Observe (4):** As replicas climb, per-pod CPU% should fall back toward 50% (the same load spread over more pods). What replica count does it stabilize at, and does the `TARGETS` number land near `50%`?

### 5. Cut the load -> after the stabilization window, scale down
```bash
kubectl delete deployment loadgen           # stop the load
kubectl get hpa podinfo -w                   # watch; the drop is NOT immediate
```
**Predict (5):** The HPA `behavior.scaleDown.stabilizationWindowSeconds` is `120`. After load stops, how long before `REPLICAS` starts falling, and what's the smallest it can go (think `minReplicas`)?

**Prove it (5):** Capture the scaling decisions the controller actually made:
```bash
kubectl describe hpa podinfo | sed -n '/Events:/,$p'
```
You should see `SuccessfulRescale` events with reasons like *"cpu resource utilization above target"* (up) and later *"All metrics below target"* (down). Convince yourself the down event lagged the load-off by ~2 min, not instantly.

### 6. (Optional) VPA in recommend mode - right-sizing, without touching pods
> Skip if the VPA controllers aren't installed (they're not in core Kubernetes / most managed clusters). This is read-only.
```bash
kubectl apply -f manifests/vpa.yaml 2>/dev/null && sleep 60 && \
kubectl describe vpa podinfo | sed -n '/Recommendation/,$p' || \
echo "VPA CRD not installed - read the manifest comments and the lecture's VPA section instead"
```
**Observe (6):** If VPA is present, `updateMode: "Off"` makes it *recommend* requests/limits in `status` without mutating pods. Note: this VPA recommends on **CPU** - the **same** resource the HPA scales on. Why would flipping it to `Auto` be dangerous here?

### 7. Push past node capacity -> pods go `Pending` -> a node is added
> Needs a growable node pool (CA on EKS / node-pool autoscaler on OVH). Tune the request in `node-pressure.yaml` to your node size so it actually overflows.
```bash
# terminal A - leave running:
kubectl get nodes -w
```
```bash
# terminal B:
kubectl apply -f manifests/node-pressure.yaml
kubectl get pods -l app.kubernetes.io/name=ballast -o wide   # some Running, some Pending
kubectl get pods -l app.kubernetes.io/name=ballast --field-selector=status.phase=Pending
kubectl describe pod -l app.kubernetes.io/name=ballast | sed -n '/Events:/,$p' | grep -i 'insufficient\|FailedScheduling\|TriggeredScaleUp' | head
```
**Predict (7):** `ballast` asks for 8 pods × `1` CPU. They won't all fit. What `STATUS` do the unschedulable pods show, what event names the reason, and what does the autoscaler do in response over the next 1-4 minutes (watch terminal A for a NEW node)?

---

## Verify
```bash
# HPA REPLICAS tracked load up and down:
kubectl describe hpa podinfo | sed -n '/Events:/,$p'        # SuccessfulRescale up AND down events
kubectl get hpa podinfo                                     # back near 1 replica at rest

# the HPA is healthy (has metrics, not <unknown>):
kubectl get hpa podinfo -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}'; echo

# a NEW node appeared under pressure (run during step 7):
kubectl get nodes                                           # one more than you started with
kubectl get pods -l app.kubernetes.io/name=ballast --field-selector=status.phase=Pending   # drains to empty as the node joins

# the PDB is guarding scale-down/drain:
kubectl get pdb podinfo                                     # ALLOWED DISRUPTIONS reflects minAvailable=1
```
yes Success = `describe hpa` shows scale-up **and** scale-down `SuccessfulRescale` events, the HPA reports a real `averageUtilization` (not `<unknown>`), a new node joined while `ballast` had `Pending` pods, and the PDB keeps at least 1 podinfo pod available.

---

## Break it - the two classic HPA failures

### B1 - HPA with NO requests -> `<unknown>`, never scales
```bash
kubectl apply -f manifests/break-no-requests.yaml
sleep 45
kubectl get hpa norequests          # TARGETS = <unknown>/50%
kubectl describe hpa norequests | sed -n '/Conditions:/,/Events:/p'
```
**Predict (B1):** The `norequests` Deployment has **no** `resources.requests.cpu`. Even under load, what does the HPA's `TARGETS` show, will `REPLICAS` ever move, and what `Condition`/`Reason` explains it?

**Prove it (B1):** The fix is to give the target a request - that's the whole point. The healthy `podinfo` HPA next to it reports a real percentage; `norequests` reports `<unknown>` purely because there's no request to divide by.
```bash
kubectl get hpa                     # compare podinfo (number) vs norequests (<unknown>)
kubectl delete -f manifests/break-no-requests.yaml
```

### B2 - target too low + no stabilization -> flapping (oscillation)
Swap the good HPA for a twitchy one **on the same `podinfo` Deployment** (target `10%`, no `behavior`):
```bash
kubectl apply -f manifests/break-flap.yaml      # same name -> replaces the good HPA
kubectl apply -f manifests/loadgen.yaml         # pulse load on
# terminal A:
kubectl get hpa podinfo -w                       # watch REPLICAS sawtooth
```
After ~2-3 minutes of watching the sawtooth, pulse the load off and on a couple of times:
```bash
kubectl scale deployment loadgen --replicas=0    # off
sleep 30
kubectl scale deployment loadgen --replicas=1    # on
```
**Predict (B2):** With the target at `10%` and **no** scale-down stabilization window, what shape does `REPLICAS` trace as metrics lag the real load - and why does a `50%` target plus a 120s window (the original `hpa.yaml`) NOT do this?

**Prove it (B2) - fix it:** put the sane HPA back and confirm the sawtooth stops:
```bash
kubectl apply -f manifests/hpa.yaml             # restore target 50% + behavior windows
kubectl get hpa podinfo -w                       # REPLICAS now settles instead of oscillating
```

---

## Cleanup
```bash
kubectl delete -f manifests/ --ignore-not-found
kubectl delete namespace lab-18-autoscaling
```
No cloud LB/volume was created. If the node-scale-out step added a node, the Cluster Autoscaler / node-pool autoscaler reclaims it a few minutes after the `ballast` pods are gone (scale-down is intentionally slow - see the lecture).

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
