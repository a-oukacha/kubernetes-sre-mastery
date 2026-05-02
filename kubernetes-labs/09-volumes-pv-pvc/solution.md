# Lab 09 - Volumes, PV/PVC & dynamic provisioning · **Solution**
**Patterns:** - (storage foundations) **Source:** KIA 6 **Est:** 55 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`, spread across AZs/zones).
- **A default StorageClass - confirm with `kubectl get sc` (one row marked `(default)`). See `../00-cluster-setup/`. On EKS that's `gp3`; on OVH set `csi-cinder-high-speed` as default (see OVH** below).

> **COST GUARD. Dynamic PVCs in this lab provision real, billed cloud disks** (EBS / Cinder). The optional **RWX** section provisions **EFS / NFS / NAS-HA, also billed. Keep every volume at 1Gi**, and run **Cleanup** the moment you finish - and confirm the backing disks are gone (the Break-it section shows you how).

## Setup
```bash
kubectl create namespace lab-09-volumes-pv-pvc
kubens lab-09-volumes-pv-pvc          # or add -n lab-09-volumes-pv-pvc to every command
kubectl get sc                        # confirm exactly one (default) StorageClass
```
**Predict (0):** Look at `kubectl get sc -o wide`. What is your default class's `RECLAIMPOLICY` and `VOLUMEBINDINGMODE`? Write both down - they decide the behavior you'll see in steps 2 and Break-it.

---

## Steps

### 1. `emptyDir` - two containers share scratch, and it dies with the pod
```bash
kubectl apply -f manifests/pod-emptydir-share.yaml
kubectl wait --for=condition=Ready pod/emptydir-share --timeout=60s
kubectl logs emptydir-share -c reader --tail=5      # the READER container
```
**Predict (1):** `writer` and `reader` are two **different containers** in one pod, mounting the *same* `emptyDir` at `/data`. Will `reader` (which never writes) show the lines `writer` is appending?

**Prove it (1):** Now destroy the pod and bring an identical one back:
```bash
kubectl exec emptydir-share -c writer -- cat /data/shared.log | tail -3   # data is here
kubectl delete pod emptydir-share
kubectl apply -f manifests/pod-emptydir-share.yaml
kubectl wait --for=condition=Ready pod/emptydir-share --timeout=60s
kubectl exec emptydir-share -c writer -- wc -l /data/shared.log           # how many lines now?
```
Convince yourself the line count **restarted from near zero** - the old `emptyDir` was destroyed with the old pod. (Bonus: `kubectl apply -f manifests/pod-emptydir-memory.yaml` then `kubectl exec emptydir-memory -- df -h /cache` - note `/cache` is a `tmpfs`, i.e. RAM.)

### 2. Create a PVC and watch binding (the trinity in action)
```bash
kubectl apply -f manifests/pvc-rwo.yaml
kubectl get pvc data-rwo                 # look at STATUS
kubectl describe pvc data-rwo | sed -n '/Events:/,$p'
```
**Predict (2):** You created the PVC but **no pod uses it yet**. Will `STATUS` be `Bound` immediately, or `Pending`? (Re-read your answer to Predict (0): what `VOLUMEBINDINGMODE` does your default SC use?)

Now create the first consumer - mounting the PVC is what triggers binding **and** provisioning of the cloud disk:
```bash
kubectl apply -f manifests/pod-writer.yaml
kubectl wait --for=condition=Ready pod/data-writer --timeout=120s
kubectl get pvc data-rwo                 # STATUS now?
kubectl get pv                           # a PV appeared, bound to data-rwo
```
**Observe (2):** The PVC flipped to `Bound` only **after** a pod consumed it, and a `PV` was dynamically created to satisfy it. Note the PV name, its `RECLAIM POLICY`, and its `STORAGECLASS`.

### 3. Write durable data
```bash
kubectl exec data-writer -- cat /data/durable.txt    # written by the pod at startup
kubectl exec data-writer -- sh -c 'echo "added live @ $(date -u)" >> /data/durable.txt; sync'
kubectl exec data-writer -- cat /data/durable.txt
```
**Observe (3): Two lines in `/data/durable.txt`. This file lives on the PV**, not in the container's writable layer.

### 4. Delete the pod -> recreate -> data persists (the whole point)
```bash
kubectl delete pod data-writer
kubectl get pvc data-rwo                 # still Bound - the claim outlives the pod
kubectl apply -f manifests/pod-reader.yaml      # a DIFFERENT pod, same claim
kubectl wait --for=condition=Ready pod/data-reader --timeout=120s
kubectl exec data-reader -- cat /data/durable.txt
```
**Predict (4):** `data-reader` never wrote anything and is a brand-new pod. What will `cat /data/durable.txt` print - empty, or both lines `data-writer` left behind?

**Prove it (4):** Both lines are there. The data survived because it lives on the PVC/PV, which is independent of any pod's lifecycle.

### 5. Expand the PVC online
```bash
kubectl get pvc data-rwo -o jsonpath='{.spec.resources.requests.storage}'; echo   # 1Gi
kubectl patch pvc data-rwo --type=merge -p '{"spec":{"resources":{"requests":{"storage":"2Gi"}}}}'
kubectl get pvc data-rwo -o wide
# Expansion completes when the controller has resized the volume AND filesystem:
kubectl get pvc data-rwo -o jsonpath='{.status.capacity.storage}'; echo
```
**Predict (5):** Your default SC needs `allowVolumeExpansion: true` for this to work. If it's `true`, will `.status.capacity.storage` reach `2Gi` immediately, or lag behind `.spec...requests.storage` for a few seconds while the CSI driver resizes?

**Observe (5):** `.spec...requests.storage` jumps to `2Gi` at once; `.status.capacity.storage` catches up once the resize finishes. If the patch is rejected with `...only dynamically provisioned pvc ... allowVolumeExpansion`, your SC forbids expansion - note that and move on.

### 6. (Optional, cloud-dependent) RWX - two pods mount one volume at once
> Only do this if you have an **RWX** StorageClass. **EKS: an `efs-rwx` class (EFS - billed). OVH:** there is **no native RWX; you must first deploy `nfs-subdir-external-provisioner` or use OVH NAS-HA (see OVH** below). Edit `storageClassName` in `manifests/pvc-rwx.yaml` to match. Skip cleanly if you don't have one.
```bash
kubectl apply -f manifests/pvc-rwx.yaml
kubectl apply -f manifests/pod-rwx-a.yaml -f manifests/pod-rwx-b.yaml
kubectl wait --for=condition=Ready pod/rwx-a pod/rwx-b --timeout=120s
sleep 6
kubectl exec rwx-b -- cat /shared/from-a.txt     # B reads A's file
kubectl exec rwx-a -- cat /shared/from-b.txt     # A reads B's file
```
**Predict (6): The same `pvc-rwo.yaml` pattern but with `accessModes: [ReadWriteMany]`. Can two pods - possibly on different nodes** - both mount it read-write? Why is the answer different from the RWO PVC in step 2?

**Prove it (6):** Each pod sees the *other's* file. RWX works because the backend is a **networked filesystem** (EFS/NFS), not a block device.

---

## Verify
```bash
# PVC bound and a PV exists for it:
kubectl get pvc data-rwo -o jsonpath='{.status.phase}'; echo            # -> Bound
kubectl get pv | grep data-rwo                                          # a Bound PV

# Data survived pod deletion (read from the SECOND pod):
kubectl exec data-reader -- cat /data/durable.txt                       # both lines present

# Expansion reflected in capacity:
kubectl get pvc data-rwo -o jsonpath='{.status.capacity.storage}'; echo # -> 2Gi (after resize)

# emptyDir was ephemeral (the recreated pod started fresh):
kubectl exec emptydir-share -c writer -- wc -l /data/shared.log         # small, not the old total

# RWX (only if you ran step 6): both pods see each other's writes:
kubectl exec rwx-b -- cat /shared/from-a.txt 2>/dev/null || echo "(RWX skipped)"
```
yes Success = `data-rwo` is `Bound` with a PV, `data-reader` reads both durable lines, capacity shows `2Gi`, and the recreated `emptydir-share` lost its old log. (RWX: both files cross-readable.)

---

## Break it - reclaim policy decides whether your data dies with the PVC

> **Small volumes only (1Gi).** This section provisions and then deletes real cloud disks on purpose.

### B1 - `reclaimPolicy: Delete` (the default) eats the disk
First, capture the PV behind your default-SC claim, then delete the claim and watch the PV go:
```bash
kubectl get pvc data-rwo -o jsonpath='PV={.spec.volumeName}{"\n"}'; echo
kubectl get pv                                  # note the PV bound to data-rwo + its RECLAIM POLICY
kubectl delete pod data-reader --ignore-not-found
kubectl delete pvc data-rwo
sleep 5
kubectl get pv                                  # is the PV still there?
```
**Predict (B1): Your default SC is almost certainly `reclaimPolicy: Delete`. After you delete the PVC, what happens to its PV - and to the cloud disk** behind it (EBS volume / Cinder volume)?

**Observe (B1):** The PV is **gone (or briefly `Released`->deleted), and the CSI driver deleted the backing disk. The data is unrecoverable**. Check your cloud console / `aws ec2 describe-volumes` / OpenStack `openstack volume list` - the volume is no longer there.

### B2 - `reclaimPolicy: Retain` keeps the disk for recovery
```bash
kubectl apply -f manifests/sc-retain.yaml       # EDIT provisioner/params for your cloud first!
kubectl apply -f manifests/pvc-retain.yaml
kubectl apply -f manifests/pod-retain-writer.yaml
kubectl wait --for=condition=Ready pod/retain-writer --timeout=120s
kubectl exec retain-writer -- cat /data/keep.txt
RETAIN_PV=$(kubectl get pvc data-retain -o jsonpath='{.spec.volumeName}'); echo "PV=$RETAIN_PV"
# Now delete the consumer and the claim:
kubectl delete pod retain-writer
kubectl delete pvc data-retain
sleep 5
kubectl get pv "$RETAIN_PV"                      # STATUS?
```
**Predict (B2):** Same delete sequence, but this PVC was bound to a `Retain` StorageClass. After deleting the PVC, what `STATUS` is the PV in, and was the cloud disk deleted?

**Observe (B2):** The PV survives as **`Released`** and the backing disk **still exists - the data is recoverable (an operator can scrub the `claimRef` and re-bind, or mount the disk out-of-band). Contrast B1 (gone) with B2 (kept): the same `kubectl delete pvc` is destructive or safe purely because of the reclaim policy.** A `Released` PV is **not** auto-recycled - you must delete it (and its disk) by hand, which is exactly how orphaned PVs and surprise bills happen.

### B3 - the multi-AZ RWO gotcha (why `WaitForFirstConsumer` exists)
You don't need to break anything here - reason it through, then confirm with placement:
```bash
kubectl get pv -o custom-columns='PV:.metadata.name,SC:.spec.storageClassName,AZ:.metadata.labels.topology\.kubernetes\.io/zone' 2>/dev/null
kubectl get nodes -L topology.kubernetes.io/zone
```
**Predict (B3): An EBS/Cinder volume physically lives in one AZ**. If the PV had been provisioned *before* scheduling (binding mode `Immediate`) and landed in `zone-a`, but the scheduler later put the pod on a node in `zone-b`, what happens to the pod? Why does `WaitForFirstConsumer` (provision *after* the scheduler picks a node) prevent this?

---

## Cloud specifics

### EKS
- **RWO (default): the `gp3` StorageClass (EBS CSI add-on) is the default - `reclaimPolicy: Delete`, `volumeBindingMode: WaitForFirstConsumer`, `allowVolumeExpansion: true`. Steps 1-5 run as written. Each EBS volume is AZ-pinned** (B3) and **billed** until deleted.
- **RWX (step 6):** install the **EFS CSI driver, create an EFS filesystem + mount targets in the cluster's VPC, and apply an `efs-rwx` StorageClass (see `../00-cluster-setup/eks.md §2.2`). Set `pvc-rwx.yaml`'s `storageClassName: efs-rwx`. EFS + mount targets are billed** - delete after.
- For `sc-retain.yaml`: `provisioner: ebs.csi.aws.com`, `parameters.type: gp3`.

### OVH
- **RWO (default): MKS ships Cinder CSI with `csi-cinder-classic` (HDD) and `csi-cinder-high-speed` (SSD), both RWO. Make one the default so step 2's PVC binds: `kubectl patch sc csi-cinder-high-speed -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'`. Cinder volumes are AZ-pinned and billed**.
- **RWX caveat (step 6):** OVH has **no native RWX block storage. To run step 6 you must first deploy `nfs-subdir-external-provisioner` (Helm) or use OVH NAS-HA** mounted via NFS, then point `pvc-rwx.yaml`'s `storageClassName` at that class (see `../00-cluster-setup/ovh.md §2.1`). If you skip RWX, the lab is otherwise complete.
- For `sc-retain.yaml`: `provisioner: cinder.csi.openstack.org`, and remove/replace the `parameters.type: gp3` line (Cinder uses different `parameters`).

---

## Cleanup
```bash
# Delete the lab's PVCs explicitly so their backing disks are released:
kubectl delete pvc --all -n lab-09-volumes-pv-pvc --ignore-not-found
# Retain PVs are NOT auto-deleted - remove any leftover Released PV + its disk:
kubectl get pv | grep -E 'lab09-retain|Released'        # find orphans
# kubectl delete pv <name>     # for each Released PV from the Retain demo
kubectl delete storageclass lab09-retain --ignore-not-found
kubectl delete namespace lab-09-volumes-pv-pvc
```
> **Confirm the disks are gone. Deleting the namespace deletes pods but a `Retain` PV (and its EBS/Cinder volume, and any EFS/NFS share) outlives it** and keeps billing. Verify in the cloud console / `aws ec2 describe-volumes --filters Name=status,Values=available` / OpenStack `openstack volume list` that no lab volume remains.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
