# Lab 09 - Volumes, PV/PVC & dynamic provisioning · **Exercise**
**Patterns:** - (storage foundations) **Source:** KIA 6 **Est:** 55 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

## Objective
Make data outlive the pod. Internalize the storage trinity - **StorageClass (the "how to provision" template), PVC** (a claim/request), **PV (the actual provisioned volume) - and watch the controller bind them. Prove that `emptyDir` is ephemeral** while a PVC-backed volume **survives pod deletion, then meet the three things that decide whether your data is safe: binding mode** (`WaitForFirstConsumer`), **reclaim policy** (`Delete` vs `Retain`), and **access mode** (RWO vs RWX).

## Concepts exercised
- `emptyDir` shared between two containers in one pod; `emptyDir.medium: Memory` (tmpfs)
- PVC + dynamic provisioning via a StorageClass and its CSI driver
- PV binding and `volumeBindingMode: WaitForFirstConsumer` (Pending until a consumer schedules)
- data persistence across pod deletion (delete pod -> recreate -> file still there)
- **online volume expansion** (`kubectl patch pvc ... storage`)
- access modes **RWO / ROX / RWX** - a property of the **backend**, not Kubernetes
- **reclaim policy** `Delete` vs `Retain` (who deletes the cloud disk, and when)
- volume **snapshots** (concept, lecture)

## Prerequisites
- Labs 01-02 done (kubectl fluency: `apply`/`describe`/`get -o jsonpath`/`patch`).
- A reachable cluster (2-3 `Ready` nodes, spread across AZs/zones).
- **A default StorageClass - exactly one StorageClass should be marked `(default)`. See `../00-cluster-setup/`. On EKS that's `gp3`; on OVH set `csi-cinder-high-speed` as default (see OVH** below).

> **COST GUARD. Dynamic PVCs in this lab provision real, billed cloud disks** (EBS / Cinder). The optional **RWX** section provisions **EFS / NFS / NAS-HA, also billed. Keep every volume at 1Gi**, and run **Cleanup** the moment you finish - and confirm the backing disks are gone (the Break-it section shows you how).

## Setup
Create a namespace **`lab-09-volumes-pv-pvc`** and make it the default for the rest of this lab. Then list the StorageClasses and confirm exactly one is marked as the default.

**Predict (0):** Inspect your default StorageClass in detail. What is its `RECLAIMPOLICY` and its `VOLUMEBINDINGMODE`? Write both down - they decide the behavior you'll see in step 2 and in Break-it.

---

## Tasks

### 1. `emptyDir` - two containers share scratch, and it dies with the pod
Apply `manifests/pod-emptydir-share.yaml` and wait for the pod `emptydir-share` to be Ready. It runs two containers (`writer` and `reader`) that mount the *same* `emptyDir` volume at `/data`. Inspect the `reader` container's recent log lines.

**Predict (1):** `writer` and `reader` are two **different containers** in one pod sharing one `emptyDir` at `/data`. Will `reader` (which never writes) show the lines `writer` is appending?

**Prove it (1): First confirm the shared log file already holds data via the `writer` container. Then destroy the pod, re-apply the same manifest, wait for Ready again, and count the lines in the shared log on the fresh pod. Convince yourself the count restarted from near zero** - the old `emptyDir` was destroyed with the old pod. (Bonus: apply `manifests/pod-emptydir-memory.yaml` and inspect the mount at `/cache` - note it is backed by `tmpfs`, i.e. RAM.)

### 2. Create a PVC and watch binding (the trinity in action)
Apply `manifests/pvc-rwo.yaml` (claim `data-rwo`) **without any pod yet**. Check the PVC's `STATUS` and read its events.

**Predict (2):** You created the PVC but **no pod uses it yet**. Will `STATUS` be `Bound` immediately, or `Pending`? (Re-read your answer to Predict (0): what `VOLUMEBINDINGMODE` does your default SC use?)

Now create the first consumer by applying `manifests/pod-writer.yaml` (pod `data-writer`) and wait for it Ready - mounting the PVC is what triggers binding **and** provisioning of the cloud disk. Re-check the PVC `STATUS` and list the PVs.

**Observe (2): Confirm the PVC flips to `Bound` only after** a pod consumes it, and that a `PV` was dynamically created to satisfy it. Note the PV name, its `RECLAIM POLICY`, and its `STORAGECLASS`.

### 3. Write durable data
The `data-writer` pod writes an initial line to `/data/durable.txt` at startup. Read that file, then append a second line of your own (and flush it), and read the file again.

**Observe (3): Two lines now sit in `/data/durable.txt`. Convince yourself this file lives on the PV**, not in the container's writable layer.

### 4. Delete the pod -> recreate -> data persists (the whole point)
Delete the `data-writer` pod and confirm the PVC is **still Bound (the claim outlives the pod). Then apply `manifests/pod-reader.yaml` - a different** pod bound to the **same** claim - wait for it Ready, and read `/data/durable.txt` from it.

**Predict (4):** `data-reader` never wrote anything and is a brand-new pod. What will `/data/durable.txt` contain - empty, or both lines `data-writer` left behind?

**Prove it (4):** Both lines are there. The data survived because it lives on the PVC/PV, which is independent of any pod's lifecycle.

### 5. Expand the PVC online
Read the PVC's currently requested storage (1Gi). Then patch the claim's requested storage up to **2Gi** and watch both the requested size (`.spec.resources.requests.storage`) and the realized capacity (`.status.capacity.storage`).

**Predict (5):** Your default SC needs `allowVolumeExpansion: true` for this to work. If it's `true`, will `.status.capacity.storage` reach `2Gi` immediately, or lag behind `.spec...requests.storage` for a few seconds while the CSI driver resizes?

**Observe (5):** Watch the requested size jump at once while the realized capacity catches up once the resize finishes. If the patch is rejected because the SC forbids expansion, note that and move on.

### 6. (Optional, cloud-dependent) RWX - two pods mount one volume at once
> Only do this if you have an **RWX** StorageClass. **EKS: an `efs-rwx` class (EFS - billed). OVH:** there is **no native RWX; you must first deploy `nfs-subdir-external-provisioner` or use OVH NAS-HA (see OVH** below). Edit `storageClassName` in `manifests/pvc-rwx.yaml` to match. Skip cleanly if you don't have one.

Apply `manifests/pvc-rwx.yaml`, then bring up both consumers `manifests/pod-rwx-a.yaml` and `manifests/pod-rwx-b.yaml` and wait for both Ready. Each writes a file into the shared mount `/shared`. Read pod A's file from pod B, and pod B's file from pod A.

**Predict (6): Same shape as `pvc-rwo.yaml` but with `accessModes: [ReadWriteMany]`. Can two pods - possibly on different nodes** - both mount it read-write? Why is the answer different from the RWO PVC in step 2?

**Prove it (6):** Each pod sees the *other's* file. RWX works because the backend is a **networked filesystem** (EFS/NFS), not a block device.

---

## Verify
Demonstrate success with observable signals: `data-rwo` reports phase `Bound` and a matching PV exists; `data-reader` reads **both** durable lines; the PVC's realized capacity shows `2Gi` after the resize; and the recreated `emptydir-share` shows a small line count (its old log was lost). If you ran step 6, both RWX pods can read each other's file.

yes Success = `data-rwo` is `Bound` with a PV, `data-reader` reads both durable lines, capacity shows `2Gi`, and the recreated `emptydir-share` lost its old log. (RWX: both files cross-readable.)

---

## Break it - reclaim policy decides whether your data dies with the PVC

> **Small volumes only (1Gi).** This section provisions and then deletes real cloud disks on purpose.

### B1 - `reclaimPolicy: Delete` (the default) eats the disk
Capture the name of the PV behind your default-SC claim `data-rwo` and note its reclaim policy. Then delete any consumer pod and the `data-rwo` PVC itself, and re-list the PVs to see whether the PV is still there.

**Predict (B1): Your default SC is almost certainly `reclaimPolicy: Delete`. After you delete the PVC, what happens to its PV - and to the cloud disk** behind it (EBS volume / Cinder volume)?

**Observe (B1):** Confirm the PV is **gone (or briefly `Released`->deleted) and that the CSI driver deleted the backing disk - the data is unrecoverable**. Verify in your cloud console (e.g. listing available EBS / Cinder volumes) that the volume is no longer there.

### B2 - `reclaimPolicy: Retain` keeps the disk for recovery
Apply `manifests/sc-retain.yaml` (a StorageClass with `reclaimPolicy: Retain` - edit its provisioner/params for your cloud first, see the cloud sections below). Apply `manifests/pvc-retain.yaml` and `manifests/pod-retain-writer.yaml`, wait for the pod Ready, and read its file at `/data/keep.txt`. Record the bound PV name. Then delete the consumer pod and the `data-retain` PVC, and inspect the recorded PV's `STATUS`.

**Predict (B2):** Same delete sequence, but this PVC was bound to a `Retain` StorageClass. After deleting the PVC, what `STATUS` is the PV in, and was the cloud disk deleted?

**Observe (B2):** Confirm the PV survives as **`Released`** and the backing disk **still exists - the data is recoverable (an operator can scrub the `claimRef` and re-bind, or mount the disk out-of-band). Contrast B1 (gone) with B2 (kept): the same delete of a PVC is destructive or safe purely because of the reclaim policy.** A `Released` PV is **not** auto-recycled - you must delete it (and its disk) by hand, which is exactly how orphaned PVs and surprise bills happen.

### B3 - the multi-AZ RWO gotcha (why `WaitForFirstConsumer` exists)
You don't need to break anything here - reason it through, then confirm with placement. Inspect the AZ/zone each PV lives in, and the zone label on each node.

**Predict (B3): An EBS/Cinder volume physically lives in one AZ**. If the PV had been provisioned *before* scheduling (binding mode `Immediate`) and landed in `zone-a`, but the scheduler later put the pod on a node in `zone-b`, what happens to the pod? Why does `WaitForFirstConsumer` (provision *after* the scheduler picks a node) prevent this?

---

## Cloud specifics

### EKS
- **RWO (default): the `gp3` StorageClass (EBS CSI add-on) is the default - `reclaimPolicy: Delete`, `volumeBindingMode: WaitForFirstConsumer`, `allowVolumeExpansion: true`. Steps 1-5 run as written. Each EBS volume is AZ-pinned** (B3) and **billed** until deleted.
- **RWX (step 6):** install the **EFS CSI driver, create an EFS filesystem + mount targets in the cluster's VPC, and apply an `efs-rwx` StorageClass (see `../00-cluster-setup/eks.md §2.2`). Set `pvc-rwx.yaml`'s `storageClassName: efs-rwx`. EFS + mount targets are billed** - delete after.
- For `sc-retain.yaml`: use provisioner `ebs.csi.aws.com` and parameter `type: gp3`.

### OVH
- **RWO (default): MKS ships Cinder CSI with `csi-cinder-classic` (HDD) and `csi-cinder-high-speed` (SSD), both RWO. Make one of them the default StorageClass so step 2's PVC binds. Cinder volumes are AZ-pinned and billed**.
- **RWX caveat (step 6):** OVH has **no native RWX block storage. To run step 6 you must first deploy `nfs-subdir-external-provisioner` (Helm) or use OVH NAS-HA** mounted via NFS, then point `pvc-rwx.yaml`'s `storageClassName` at that class (see `../00-cluster-setup/ovh.md §2.1`). If you skip RWX, the lab is otherwise complete.
- For `sc-retain.yaml`: use provisioner `cinder.csi.openstack.org`, and remove/replace the `type: gp3` parameter (Cinder uses different `parameters`).

---

## Cleanup
Delete the lab's PVCs explicitly so their backing disks are released. Remember that **`Retain`** PVs are **not** auto-deleted - find any leftover `Released` PV from the Retain demo and remove it (and its disk) by hand. Then delete the `lab09-retain` StorageClass and finally the `lab-09-volumes-pv-pvc` namespace.

> **Confirm the disks are gone. Deleting the namespace deletes pods but a `Retain` PV (and its EBS/Cinder volume, and any EFS/NFS share) outlives it** and keeps billing. Verify in the cloud console (e.g. by listing available EBS / Cinder volumes) that no lab volume remains.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
