# Lab 02 - Deployments, ReplicaSets, rolling updates & rollback · **Solution**
**Patterns:** Declarative Deployment **Source: KIA 9, KP "Declarative Deployment" Est:** 50 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

## Objective
Ship a replicated stateless service, push a new version with **zero downtime, watch the rollout surge pod-by-pod, then deliberately ship a bad release - observe the rollout safely stall** instead of taking traffic - and **roll back**.

## Concepts exercised
- Deployment -> ReplicaSet -> Pod ownership; the `pod-template-hash` label
- `strategy.RollingUpdate` (`maxSurge`, `maxUnavailable`) and `minReadySeconds`
- Why a **readiness probe** gates a rollout's progress
- `kubectl rollout status` / `history` / `undo`; `revisionHistoryLimit`
- `kubectl set image` vs `apply` as ways to trigger a new revision
- Measuring availability through a change with an in-cluster `fortio` load loop

## Prerequisites
- Lab 01 done (labels, selectors, `describe`/events, the declarative model).
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`). See `../00-cluster-setup/`.

## Setup
```bash
kubectl create namespace lab-02-deployments
kubens lab-02-deployments          # or add -n lab-02-deployments to every command
```
No LoadBalancer or volume is created in this lab - the load loop runs **in-cluster** against a `ClusterIP` Service, so there is no cloud cost to flag.

---

## Steps

### 1. Deploy v1 (3 replicas) and its Service
```bash
kubectl apply -f manifests/deploy-v1.yaml
kubectl rollout status deployment/podinfo      # blocks until all 3 are Ready
kubectl get deploy,rs,pods --show-labels
```
**Predict (1):** One `kubectl apply` created a Deployment. How many ReplicaSets and how many Pods now exist, and which object *owns* which? Check `kubectl get rs` and the `OWNER`/`CONTROLLED BY` lines in `kubectl describe pod <one-pod>`.

### 2. Find the `pod-template-hash` - the link between RS and its pods
```bash
kubectl get rs -L pod-template-hash
kubectl get pods -L pod-template-hash
kubectl get rs -o jsonpath='{.items[0].spec.selector.matchLabels}'; echo
```
**Observe (2):** The ReplicaSet's name ends in a hash, and every pod carries a `pod-template-hash` label with that *same* value. Note where this label came from - you never wrote it in `deploy-v1.yaml`. Where do you think it's computed from?

### 3. Start a load loop so you can *measure* the next rollout
```bash
kubectl apply -f manifests/loadgen.yaml
sleep 10
kubectl logs loadgen --tail=15        # fortio prints a running tally; note "Code 200" count climbing
```
**Observe (3): `loadgen` is a fortio pod hitting `http://podinfo:9898/` at 50 req/s, forever. Confirm it is getting `200`s. Leave it running - you'll read its failure count** after each rollout.

### 4. Roll out v2 - watch the surge happen pod-by-pod
In one terminal start a watch on the ReplicaSets, then trigger the update in another:
```bash
# terminal A - leave running:
kubectl get rs -w -L pod-template-hash
```
```bash
# terminal B:
kubectl apply -f manifests/deploy-v2.yaml      # or: kubectl set image deploy/podinfo podinfo=ghcr.io/stefanprodan/podinfo:6.7.1
kubectl rollout status deployment/podinfo      # narrates each step; ends "successfully rolled out"
```
**Predict (4):** The strategy is `maxSurge: 1, maxUnavailable: 0`. During the update, what is the *maximum* total number of podinfo pods you'll see at once, and the *minimum* number of **Ready** pods at any instant? Watch terminal A to check.

### 5. Confirm zero dropped requests across the v2 rollout
```bash
kubectl logs loadgen --tail=20
# look at the summary block fortio prints periodically:
#   "Code 200 : N"   and   "Code -1 / non-200" lines
```
**Prove it (5): The `Code 200` counter kept climbing across the rollout and there are no** (or vanishingly few, from in-flight connection resets) non-200 codes. Prove the update was zero-downtime: capture the failure count, not just "it looked fine."

### 6. Inspect rollout history and the now-superseded ReplicaSet
```bash
kubectl rollout history deployment/podinfo
kubectl get rs -L pod-template-hash            # old RS still exists, scaled to 0
```
**Observe (6): There are now two revisions and two ReplicaSets - the old one scaled to `0` but kept**. What is the old, zeroed-out ReplicaSet *for*? (You're about to use it.)

### 7. Ship the broken v3 - readiness probe that always 404s
```bash
kubectl apply -f manifests/deploy-v3-broken.yaml
kubectl rollout status deployment/podinfo --timeout=60s    # will NOT succeed
```
**Predict (7):** v3's image and process are fine, but its readiness probe targets a path that returns 404. With `maxUnavailable: 0`, predict: does `rollout status` ever print "successfully rolled out"? How many *new* pods get created, and how many of them reach `Ready`? Does the Service keep serving v2 traffic meanwhile?

---

## Verify
```bash
# the good v2 rollout succeeded (run BEFORE step 7, or after the undo in Break-it):
kubectl rollout status deployment/podinfo                         # -> "successfully rolled out"
kubectl get deploy podinfo -o jsonpath='{.status.readyReplicas}/{.status.replicas} ready'; echo   # -> 3/3 ready

# zero-downtime signal from the load loop:
kubectl logs loadgen | grep -E 'Code (200|-1)|non-2xx' | tail -5  # 200s climbing, ~0 failures
```
yes Success = `rollout status` reports the good version rolled out, `3/3` ready, and the fortio loop shows essentially no failed requests across the change.

---

## Break it - a bad release must stall, not serve
You already shipped v3 in step 7. Now watch the safety property hold.
```bash
kubectl get rs -L pod-template-hash        # new (v3) RS exists but READY stays 0
kubectl get pods -L pod-template-hash      # v3 pods Running but 0/1 READY; v2 pods still Ready
kubectl rollout status deployment/podinfo  # hangs - Ctrl-C; it never completes
```
**Predict (B1):** A v3 pod shows `Running` with `READY 0/1`. Is it in the Service's endpoints? Run `kubectl get endpoints podinfo -o wide` (or `kubectl get endpointslices -l kubernetes.io/service-name=podinfo`) and decide whether any v3 pod IP is listed.

**Observe (B2):** Read why the rollout is stuck - Deployment conditions tell the story:
```bash
kubectl describe deploy podinfo | sed -n '/Conditions:/,/Events:/p'
kubectl rollout status deployment/podinfo --timeout=5s   # prints "Waiting for ... new replicas ... to be available"
```
What `Progressing` condition / `Reason` does the Deployment report, and why does the load loop still show no errors even though a "deploy" is in progress?

Now roll back - the move that makes this safe:
```bash
kubectl rollout undo deployment/podinfo
kubectl rollout status deployment/podinfo                # -> "successfully rolled out" (back on v2)
kubectl logs loadgen --tail=20 | grep -E 'Code (200|-1)'  # failures still ~0 across the whole episode
```
**Prove it (B3):** After `undo`, the deployment is `3/3` Ready on v2 again and the v3 ReplicaSet is scaled back to `0`. Confirm with `kubectl rollout history deployment/podinfo` and `kubectl get rs -L pod-template-hash`. Did the broken release ever serve a single request to the load loop?

---

## Cleanup
```bash
kubectl delete namespace lab-02-deployments
```
No cloud LB/volume was created in this lab - deleting the namespace is enough.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
