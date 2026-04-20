# Lab 02 - Deployments, ReplicaSets, rolling updates & rollback · **Exercise**
**Patterns:** Declarative Deployment **Source: KIA 9, KP "Declarative Deployment" Est:** 50 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

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
- A reachable cluster (2-3 `Ready` nodes). See `../00-cluster-setup/`.

## Setup
Create a namespace **`lab-02-deployments` and make it the default for the rest of this lab (so you don't have to pass `-n` every time). No LoadBalancer or volume is created in this lab - the load loop runs in-cluster** against a `ClusterIP` Service, so there is no cloud cost to flag.

---

## Tasks

### 1. Deploy v1 (3 replicas) and its Service
Apply `manifests/deploy-v1.yaml`, wait until the rollout reports all three replicas Ready, then list the Deployment, ReplicaSets, and Pods together with their labels.

**Predict (1):** One apply created a Deployment. How many ReplicaSets and how many Pods now exist, and which object *owns* which? Inspect the ReplicaSet list and the ownership/`CONTROLLED BY` lines on one pod to confirm.

### 2. Find the `pod-template-hash` - the link between RS and its pods
Surface the `pod-template-hash` label as its own column on both the ReplicaSets and the Pods, and read the ReplicaSet's `spec.selector.matchLabels`.

**Observe (2):** The ReplicaSet's name ends in a hash, and every pod carries a `pod-template-hash` label with that *same* value. Note that you never wrote this label in `deploy-v1.yaml`. Where do you think it's computed from?

### 3. Start a load loop so you can *measure* the next rollout
Apply `manifests/loadgen.yaml`, give it a few seconds, then read its recent logs. `loadgen` is a fortio pod hitting `http://podinfo:9898/` at 50 req/s, forever.

**Observe (3): Confirm it is getting `200`s and that the count is climbing. Leave it running - you'll read its failure count** after each rollout.

### 4. Roll out v2 - watch the surge happen pod-by-pod
In one terminal, start a live watch on the ReplicaSets (with the `pod-template-hash` column shown). In another, trigger the v2 update - either by applying `manifests/deploy-v2.yaml` or by setting the `podinfo` container image to `ghcr.io/stefanprodan/podinfo:6.7.1` - and follow the rollout to completion.

**Predict (4):** The strategy is `maxSurge: 1, maxUnavailable: 0`. During the update, what is the *maximum* total number of podinfo pods you'll see at once, and the *minimum* number of **Ready** pods at any instant? Watch the ReplicaSet view to check.

### 5. Confirm zero dropped requests across the v2 rollout
Read the loadgen logs again and find the periodic fortio summary block - specifically its `Code 200` count and any `Code -1` / non-200 lines.

**Prove it (5): The `Code 200` counter kept climbing across the rollout and there are no** (or vanishingly few, from in-flight connection resets) non-200 codes. Prove the update was zero-downtime: capture the failure count, not just "it looked fine."

### 6. Inspect rollout history and the now-superseded ReplicaSet
Show the Deployment's rollout history, and list the ReplicaSets again with the `pod-template-hash` column.

**Observe (6): There are now two revisions and two ReplicaSets - the old one scaled to `0` but kept**. What is the old, zeroed-out ReplicaSet *for*? (You're about to use it.)

### 7. Ship the broken v3 - readiness probe that always 404s
Apply `manifests/deploy-v3-broken.yaml` and start watching the rollout (with a short timeout - it will not succeed).

**Predict (7):** v3's image and process are fine, but its readiness probe targets a path that returns 404. With `maxUnavailable: 0`, predict: does the rollout ever report "successfully rolled out"? How many *new* pods get created, and how many of them reach `Ready`? Does the Service keep serving v2 traffic meanwhile?

---

## Verify
Demonstrate success with observable signals: confirm the good v2 rollout completed (run this **before** step 7, or after the undo in Break-it); confirm the Deployment reads `3/3` ready; and confirm the load loop shows `200`s climbing with essentially no failures across the change.

yes Success = the rollout reports the good version rolled out, `3/3` ready, and the fortio loop shows essentially no failed requests across the change.

---

## Break it - a bad release must stall, not serve
You already shipped v3 in step 7. Now watch the safety property hold: list the ReplicaSets and the Pods (with `pod-template-hash`) and observe the v3 RS exists but its `READY` stays `0`, the v3 pods are `Running` but `0/1` Ready, and the v2 pods are still Ready. Confirm the rollout never completes.

**Predict (B1):** A v3 pod shows `Running` with `READY 0/1`. Is it in the Service's endpoints? Inspect the Service's endpoints (or its EndpointSlices) and decide whether any v3 pod IP is listed.

**Observe (B2):** Read why the rollout is stuck - the Deployment's `Conditions:` block tells the story. What `Progressing` condition / `Reason` does the Deployment report, and why does the load loop still show no errors even though a "deploy" is in progress?

Now roll back - the move that makes this safe. Undo the rollout, wait for it to settle back on v2, and re-read the loadgen failure count.

**Prove it (B3):** After the undo, the deployment is `3/3` Ready on v2 again and the v3 ReplicaSet is scaled back to `0`. Confirm with the rollout history and the ReplicaSet list. Did the broken release ever serve a single request to the load loop?

---

## Cleanup
Delete the `lab-02-deployments` namespace. No cloud LB/volume was created here, so that's enough.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
