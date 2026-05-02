# Lecture 09 - Volumes, PV/PVC & dynamic provisioning: where state lives

## Answers to the lab checkpoints
- **(0)** On EKS the default `gp3` class is `RECLAIMPOLICY=Delete`, `VOLUMEBINDINGMODE=WaitForFirstConsumer`. Most managed defaults look like this. The reclaim policy is *why* deleting a PVC can destroy data (Break-it B1); the binding mode is *why* the PVC in step 2 stayed `Pending` until a pod consumed it.
- **(1)** **Yes. `writer` and `reader` are separate containers but share one** `emptyDir` mounted at `/data` in both. A volume is a pod-level object; containers that mount it see the same files. This is the canonical sidecar pattern (one container produces, another consumes). After the delete+recreate, the line count restarts near zero - the old `emptyDir` was destroyed *with the pod*. `emptyDir` is scratch space tied to the pod's life, not durable storage.
- **(2)** **Pending, then `Bound` only after `data-writer` is created. With `volumeBindingMode: WaitForFirstConsumer` the binding/provisioning is deliberately delayed until a pod that wants the PVC is scheduled, so the volume can be created in the same AZ** as the node the pod landed on. A PVC with an `Immediate` SC would bind right away.
- **(3)** `/data/durable.txt` holds the startup line plus the line you appended. It lives on the PV (the mounted volume), not in the container's ephemeral writable layer - that's the difference that makes step 4 work.
- **(4)** **Both lines print. `data-reader` is a brand-new pod that never wrote anything, yet it sees the file because it mounts the same PVC**. The data is bound to the claim/PV, which is independent of any pod. This is *the* lesson: pods are cattle, the PVC is the pet that holds state.
- **(5) `.spec...requests.storage` jumps to `2Gi` immediately (you patched the desired state); `.status.capacity.storage` lags a few seconds while the CSI driver grows the block device and then the filesystem, then catches up. Online expansion needs `allowVolumeExpansion: true` on the SC. You can only grow, never shrink.**
- **(6)** Yes - two pods, even on different nodes, mount it read-write simultaneously, because the claim is `ReadWriteMany` and the backend is a **networked filesystem (EFS/NFS). The RWO PVC in step 2 could not do this: block storage (EBS/Cinder) attaches to one node at a time**. Access mode is a property of the **backend**, not a flag you can wish onto block storage.
- **(B1)** The PV is **deleted** and the CSI driver **deletes the backing cloud disk** (EBS/Cinder volume). With `reclaimPolicy: Delete`, the PV's lifecycle is tied to the PVC - delete the claim, lose the disk and all data. Unrecoverable.
- **(B2)** The PV moves to **`Released`** (claim gone, but PV and disk **kept**). Nothing auto-recycles it. The data is recoverable by an operator. The *only* difference from B1 is the reclaim policy on the StorageClass that provisioned it.
- **(B3) With `Immediate` binding the pod would be `Pending` forever** (or fail to attach): a block volume lives in one AZ, and a pod scheduled into a *different* AZ can't attach it - you'd see `FailedAttachVolume` / `volume node affinity conflict`. `WaitForFirstConsumer` provisions the volume *after* the scheduler picks the node, guaranteeing the disk is created in the node's AZ. This is why nearly every modern cloud default SC uses it.

---

## What just happened (under the hood)
The storage model is three objects with one controller stitching them together:

1. StorageClass = the "how to provision" template. It names a **CSI driver** (`provisioner:`), a `reclaimPolicy`, a `volumeBindingMode`, `allowVolumeExpansion`, and driver `parameters` (disk type, IOPS, fs). You usually never touch it - the platform ships a default.
2. **PVC = a claim/request. "I want 1Gi, RWO, from the default class." It is namespaced** and lives with the app. The PVC is the durable handle your pods reference; pods come and go, the PVC stays.
3. **PV = the actual provisioned volume.** A **cluster-scoped** object representing one real disk. With *dynamic* provisioning you never write a PV by hand - the controller creates it to satisfy a PVC.

The flow you watched: you create a PVC -> the **PV controller sees an unbound claim -> because the SC is `WaitForFirstConsumer`, it waits**. You create a pod that mounts the PVC -> the scheduler picks a node -> *now* the controller asks the **CSI driver** to provision a real disk **in that node's topology (AZ)**, creates a PV, and **binds PVC<->PV (a 1:1 relationship via `claimRef`/`volumeName`). The kubelet's CSI plugin then attaches** the disk to the node and **mounts** it into the container at your `mountPath`.

When you delete the PVC, the **reclaim policy** on the PV decides the endgame: `Delete` -> CSI deletes the PV *and* the cloud disk; `Retain` -> PV becomes `Released`, disk kept, human cleans up. **Expansion** is the same loop in reverse: you edit the PVC's requested size, the CSI driver grows the disk and (for filesystem volumes) the filesystem online.

Two durable lessons:
- The container filesystem and `emptyDir` are EPHEMERAL. Anything not on a PVC is gone when the pod dies. If it matters, it's on a claim.
- RWO vs RWX is the BACKEND's property. Block storage (EBS/Cinder) = single-node attach = RWO. Networked filesystems (EFS/NFS) = multi-node = RWX. You cannot make a block device RWX by changing a YAML field.

## Dev notes
- **Prefer stateless.** Push state to a managed datastore or object store and keep pods disposable; it makes scaling, rollout, and recovery trivial. Reach for a PVC only when you genuinely need node-local durable state.
- **`emptyDir` is scratch, not storage. Caches, unzip targets, a sidecar's hand-off dir - fine. Anything you'd be sad to lose on a pod restart does not belong there. `emptyDir.medium: Memory` is a RAM-backed `tmpfs`: fast, counts against your memory limit**, and just as ephemeral.
- Reference PVCs by name; don't hardcode PVs. Let dynamic provisioning do the work. In a StatefulSet (lab 10) you'll use `volumeClaimTemplates` to mint one PVC per replica automatically.
- `accessModes` is a request the backend must be able to honor. Ask for `ReadWriteMany` against a block-only SC and your PVC sits `Pending` forever.

## DevOps / Platform notes
- StorageClass design is platform work. Offer tiers (fast SSD vs cheap HDD), set a sane **default**, decide `reclaimPolicy` per class (most teams keep `Delete` for dev, `Retain` for anything stateful in prod), enable `allowVolumeExpansion`, and standardize on `WaitForFirstConsumer`.
- **CSI is the universal interface.** EBS, Cinder, EFS, NFS, Ceph - all speak CSI. Snapshots, expansion, and topology awareness are CSI features your driver may or may not implement; check capabilities before promising them.
- **Snapshots (`VolumeSnapshot` / `VolumeSnapshotClass`) give point-in-time copies for clone/restore - but a snapshot is not a backup (same blast radius as the cluster/account). Use Velero** (or similar) to back up PV data *and* the Kubernetes objects off-cluster.
- Online expansion is one-way (grow only). Provision small, expand later. Plan capacity alerts on PV usage; a full disk is an outage, not a warning.

## Architect notes (trade-offs)
- Decide where state lives, deliberately. In-cluster PVs are simple to wire but make you the DBA (backup, HA, upgrades). An external managed DB / object store moves that burden to the provider and is usually the right call for primary data. Use in-cluster PVs for things that are *naturally* node-local (caches, scratch, single-writer queues).
- **RWX is expensive and not universal. EFS/NFS cost more and are slower than block storage, and OVH has no native RWX at all**. Architect around **RWO** when you can (one writer + readers via the app, or sharded per-pod volumes à la StatefulSet) and treat RWX as a deliberate, costed choice.
- Topology is a constraint, not a detail. Block volumes are AZ-pinned. Multi-AZ resilience for stateful workloads means either replicating at the app layer (e.g. a 3-node quorum across AZs, each with its own RWO volume) or accepting that a pod's data is tied to one AZ.

## SRE notes (failure modes, SLOs, toil)
- **Top storage failure modes:** **full disk (writes fail, app wedges - alert on PV utilization, not just node disk); stuck attach/detach (`FailedAttachVolume`, `Multi-Attach error` when a block volume is still attached to a dead node - the volume must detach before the rescheduled pod can mount it); orphaned PVs after a hurried namespace delete left `Retain` PVs (and their bills) behind; volume node-affinity conflict** (the multi-AZ RWO gotcha from B3).
- Reclaim-policy mistakes are a top data-loss cause. A `Delete`-class PVC deleted in a cleanup script takes the disk with it. Anything precious belongs on a `Retain` class - and `Retain` then becomes *toil*, because someone must reclaim or delete those PVs by hand.
- Backup/restore is a DRILL, not a config. An untested backup is not a backup. Schedule real restore exercises (restore a Velero snapshot into a scratch namespace and validate the data). Track restore time as an SLO input for your RTO.
- **Capacity is a reliability concern.** Page on "PV >85% full" with enough lead time to run an online expansion before writes fail.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- **Model-weights volume = RWX, shared **read**.** Many inference replicas mount the *same* weights read-only (`ReadOnlyMany` / a shared RWX FS), so you store the multi-GB checkpoint once and fan it out to every server. EFS/NFS is the typical backend; on RWO-only clouds you instead bake weights into the image or pull from object storage at startup.
- **Large dataset volumes** for training/eval are classic PVC territory - provision big, expand as the corpus grows, snapshot for reproducibility.
- `emptyDir.medium: Memory` (tmpfs) for KV-cache / scratch. Decode-time KV cache and other hot, throwaway intermediates want RAM speed and are fine to lose on pod death - exactly what a Memory-medium `emptyDir` provides (just remember it eats the pod's memory limit).
- The multi-AZ RWO problem hits GPU pods too. A volume must be in the GPU node's AZ; with scarce GPU capacity in only some AZs, `WaitForFirstConsumer` is what keeps the volume from being stranded away from the only node that can run the pod.

## Pitfalls
- Assuming RWX is available everywhere. It isn't - **OVH has no native RWX**; you must run an NFS provisioner or NAS-HA. Design for RWO first.
- `reclaimPolicy: Delete` eating data. Deleting a PVC on a `Delete` class destroys the disk. Use `Retain` for anything you can't recreate - and remember to clean up the resulting `Released` PVs.
- Expecting `emptyDir` (or the container fs) to persist. It vanishes with the pod. Real state goes on a PVC.
- **PVC stuck `Pending`.** Usual causes: no default StorageClass; an `accessMode` the backend can't satisfy (RWX on block storage); the SC is `WaitForFirstConsumer` and nothing has consumed the claim yet (this one is *expected* - create the pod).
- Forgetting volumes are billed after the namespace is gone. `Retain` PVs and their cloud disks (and EFS/NFS shares) outlive `kubectl delete namespace`. Delete PVCs explicitly and verify in the cloud console.

## Further reading
- **KIA ch6** - Volumes: `emptyDir`, `hostPath`, PersistentVolumes, PersistentVolumeClaims, StorageClasses, dynamic provisioning.
- Kubernetes docs: *Persistent Volumes*, *Storage Classes*, *Volume Snapshots*, *CSI*. You'll build directly on this in lab 10 (StatefulSets, `volumeClaimTemplates`).
