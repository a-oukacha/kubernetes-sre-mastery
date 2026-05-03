# Lab 10 - StatefulSets · **Exercise**
**Patterns:** Stateful Service **Source:** KIA 10; KP "Stateful Service" **Est:** 55 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

## Objective
Run apps that need three things a Deployment can't give them: a **stable network identity (a name + DNS that survives reschedule), stable per-pod storage (the same disk re-attaches to the same ordinal), and an ordered, at-most-one lifecycle** (0->1->2 up, reverse down; never two pods sharing one identity). You'll watch ordered creation, prove each ordinal owns exactly one PVC, resolve a single pod by DNS, kill a pod and watch it return with the *same* name and the *same* data, and canary a change to the highest ordinal first with a partitioned update.

## Concepts exercised
- StatefulSet vs Deployment: stable ordinal names (`<sts>-0..n`), not random hashes
- `volumeClaimTemplates` - one PVC provisioned **per ordinal**, re-attached on reschedule
- headless Service (`clusterIP: None`) is **required** for stable per-pod DNS
- stable per-pod DNS: `<sts>-0.<svc>.<ns>.svc.cluster.local`
- ordered creation/deletion + `podManagementPolicy` (`OrderedReady` vs `Parallel`)
- the "at most one pod per ordinal at a time" guarantee
- partitioned rolling update (`updateStrategy.rollingUpdate.partition`)
- scale-down and StatefulSet deletion **retain** PVCs (data safety + a cost trap)

## Prerequisites
- Labs **01 (labels, declarative model, `describe`/events), 06** (headless Service & cluster DNS), **09** (PV/PVC, dynamic provisioning, default StorageClass) done.
- A reachable cluster with a **default RWO StorageClass** (one marked `(default)`). See `../00-cluster-setup/`.

## Setup
Create a namespace **`lab-10-statefulsets`** and make it the default for the rest of this lab. Then apply, in order, the three manifests this lab ships: the headless Service `manifests/svc-headless.yaml` (required for stable per-pod DNS), the StatefulSet `manifests/statefulset.yaml` (3 redis replicas, one PVC per ordinal), and the `manifests/client.yaml` busybox pod you'll use for DNS lookups. Wait until the client pod is `Ready` before moving on.

> **COST GUARD.** `volumeClaimTemplates` provisions **one real cloud disk per replica** (3 replicas -> **3 billed disks**). These PVCs are **not** deleted when you scale down, and **not** deleted when you delete the StatefulSet - Cleanup deletes them explicitly. Don't walk away without running Cleanup.

**Predict (0):** You just applied a StatefulSet with `replicas: 3` and `podManagementPolicy: OrderedReady`. Will all three pods appear at once, or one at a time? In what name order? Watch the pods come up and check whether you were right.

---

## Tasks

### 1. Watch ORDERED creation: 0 -> 1 -> 2
Watch the StatefulSet's pods appear and reach `Running`. Don't snapshot once - observe the sequence in which they are created.

**Observe (1):** What are the pod **names** (compare to a Deployment's random suffixes)? Under `OrderedReady`, what had to be true of one pod before the next was created? Note which pod reached `Running` first.

### 2. Each ordinal gets its OWN PVC
List the PersistentVolumeClaims belonging to the StatefulSet, including each claim's bound volume.

**Observe (2):** How many PVCs exist, and how are they named relative to the StatefulSet and ordinal? Confirm each is `Bound` to its own distinct PersistentVolume - there is no shared disk here.

### 3. Resolve a SINGLE pod by stable DNS
From the client pod, resolve the per-pod DNS name of an individual ordinal, then resolve the bare headless Service name. Also list the StatefulSet pods with their IPs so you can cross-check.

**Predict (3):** A per-pod DNS name like `kvstore-0.kvstore` - how many `Address:` lines should it return, and whose IP is it? How many addresses does the bare headless Service name return? Confirm the per-pod name resolves to exactly that pod's IP.

### 4. Write DISTINCT data into each pod's volume
Each pod runs redis with its data dir on its own PVC. Stamp a unique marker into each pod (e.g. an `owner` key set to that pod's name, plus its ordinal), then read the markers back and also display the on-disk identity file (`/data/identity.txt`) each pod wrote on first boot.

**Prove it (4):** Show that each pod returns its *own* marker value and its *own* `identity.txt` line. Argue why this is necessarily per-pod: each ordinal has its own PVC, so there is no shared storage.

### 5. Scale DOWN to 1 - watch reverse order, PVCs REMAIN
Scale the StatefulSet down to a single replica while watching the pods, then list the remaining pods and the PVCs.

**Predict (5):** Scaling 3->1 removes pods in which order? After scale-down, how many PVCs remain - 1 or 3? Check and explain what happened to the disks for ordinals 1 and 2.

### 6. Scale back to 3 - original PVCs + data RE-ATTACH
Scale the StatefulSet back up to 3 and wait for the rollout to finish. Then read back the `owner` marker and `identity.txt` from the pods that had disappeared.

**Prove it (6): Show that the returned ordinals carry their original** marker and **original** `identity.txt` (same first-boot timestamp). Explain which PVC each re-bound and why identity *and* data survived the scale-down/up cycle.

### 7. Partitioned rolling update - canary the HIGHEST ordinal first
Apply `manifests/statefulset-partition.yaml`, which adds the env var `REDIS_BUILD=canary` and sets `updateStrategy.rollingUpdate.partition: 2`. After the rollout settles, inspect each pod to see which ones carry the new env var.

**Predict (7): With `partition: 2`, which ordinals get the new `REDIS_BUILD=canary` env, and which stay on the old spec? Then promote the rollout by patching the partition down to 0, wait for it, and re-check every pod. Note the update order** when the partition drops to 0 - which ordinal updates last?

---

## Verify
Demonstrate success with observable signals: exactly one PVC per ordinal (three total, named per-ordinal); the per-pod DNS name for an ordinal resolves to that specific pod's IP; and after deleting and letting one ordinal recreate, it comes back with the **same** name, the **same** PVC, and the **same** data.

yes Success = **3** PVCs, one per ordinal; per-pod DNS resolves to the matching pod's IP; after delete+recreate, the ordinal keeps its name, PVC, and data.

---

## Break it - identity & storage survive; a Deployment's don't

### Break-it A - delete a StatefulSet pod -> SAME identity + SAME disk
Note the UID of one ordinal, delete that pod, watch it return, then read back its `owner` marker and `identity.txt`.

**Predict (B1): The replacement is a brand-new pod (new UID). What name** will it get, and will it have the data you wrote in Task 4? Why can the controller reuse that ordinal's PVC?

### Break-it B - the same app as a Deployment: random name, EMPTY volume
Apply `manifests/deploy-contrast.yaml` (the same redis app, but as a Deployment using an `emptyDir`). Capture the running pod's name, write an `owner` marker into it, delete it, let the Deployment replace it, and read the marker from the new pod.

**Predict (B2): Compare the Deployment's replacement to the StatefulSet's. Does the new pod have the same name** as the old one? Does it still have the data you set? State the two guarantees the StatefulSet gave that the Deployment did not.

### Break-it C - scale to 0, then back: data returns; deletion orphans PVCs
Scale the StatefulSet to 0 and confirm no pods remain but the PVCs do; scale back to 3, wait, and confirm the data returned. Then delete the StatefulSet itself (not the PVCs) and list the PVCs again.

**Predict (B3):** After deleting the StatefulSet, how many PVCs remain? Are the underlying cloud disks still provisioned (and billed)? What single Cleanup command actually frees them?

---

## Cleanup
StatefulSet PVCs are **not** garbage-collected, so the cloud disks linger and keep billing unless you remove them explicitly. Delete the lab's manifests, then delete the StatefulSet's PVCs by label, then delete the `lab-10-statefulsets` namespace (which also removes any remaining PVCs and releases their disks under the default `Delete` reclaim policy). Finally, confirm no `kvstore` PVCs remain anywhere in the cluster.

> The headless Service and client cost nothing. **The per-ordinal PVCs are the cost** - three RWO cloud disks. They survive scale-down and StatefulSet deletion by design (data safety); only deleting the PVCs/namespace releases them.

### EKS
`volumeClaimTemplates` uses the default RWO StorageClass **gp3** (EBS CSI). EBS volumes are **zonal: each ordinal's PVC pins that ordinal's pod to the AZ** where its disk lives, so a rescheduled ordinal must land in the same AZ as its disk. Multi-AZ node groups still satisfy this because the scheduler honors the volume's topology.

### OVH
Default RWO StorageClass is **csi-cinder-high-speed (Cinder CSI). Cinder volumes are likewise zonal**: each ordinal's disk pins its pod to that zone on reschedule. Confirm a default SC exists; if none is marked `(default)`, set `csi-cinder-high-speed` as default first.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
