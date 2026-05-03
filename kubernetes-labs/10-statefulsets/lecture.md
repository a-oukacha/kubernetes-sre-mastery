# Lecture 10 - StatefulSets: stable identity, stable storage, ordered lifecycle

## Answers to the lab checkpoints
- **(0) One at a time, in ordinal order: `kvstore-0`, then `kvstore-1`, then `kvstore-2`. With `podManagementPolicy: OrderedReady` the controller will not create `kvstore-1` until `kvstore-0` is Ready**, and so on. (With `Parallel` they'd all appear at once.)
- **(1)** Names are **stable ordinals**, not random hashes - `kvstore-0/1/2`. `kvstore-0` went Running first; each later ordinal waited for its predecessor's readiness. The name is part of the pod's *identity*, not a coincidence.
- **(2)** Exactly **three** PVCs, one per ordinal: `data-kvstore-0/1/2`, named `<volumeClaimTemplate>-<sts>-<ordinal>`. Each is `Bound` to its own PV (its own cloud disk). The template is instantiated once per replica - there is no shared volume.
- **(3)** `kvstore-0.kvstore` returns **one `Address` - `kvstore-0`'s pod IP. The bare `kvstore` (headless Service) returns all** ready pods' IPs (here 3). The headless Service is what publishes those per-pod A records; without it there is no `<pod>.<svc>` name.
- **(4) Each pod returns its own `owner` and its own `identity.txt`. Because every pod mounts its own** PVC at `/data`, the writes never collide - this is per-pod storage, not a shared disk.
- **(5)** Scale 3->1 deletes in **reverse ordinal order: `kvstore-2` first, then `kvstore-1`. Three PVCs still remain - scale-down deletes pods but retains** the PVCs (and disks) for ordinals 1 and 2 by design. The data is parked, waiting.
- **(6) `kvstore-1` and `kvstore-2` re-bind their original** PVCs (`data-kvstore-1`, `data-kvstore-2`), so `owner` and the first-boot `identity.txt` come back unchanged. Identity = name + DNS + storage, and all three were preserved.
- **(7)** With `partition: 2`, **only `kvstore-2` gets `REDIS_BUILD=canary`; `kvstore-0` and `kvstore-1` stay on the old spec. Lowering the partition to 0 rolls the change down to the rest in reverse** ordinal order (`kvstore-1`, then `kvstore-0` last). Highest ordinal is your canary; ordinal 0 changes last.
- **(B1)** Same **name** (`kvstore-1`) and same data. The controller guarantees "at most one pod per ordinal," so it recreates the *same identity*; the PVC `data-kvstore-1` was never deleted, so the new pod re-mounts the same disk with `owner=kvstore-1` intact.
- **(B2) The Deployment's replacement has a different name (random `<hash>-<rand>` suffix) and an empty volume - its `emptyDir` was destroyed with the old pod, so `owner` is gone. The StatefulSet gave two things the Deployment did not: a stable name/identity** and **stable per-pod storage**.
- **(B3)** **Three** PVCs remain after deleting the StatefulSet - they are *not* garbage-collected, and the cloud disks are still provisioned and **billed**. Only `kubectl delete pvc ...` (or deleting the namespace) releases them.

---

## What just happened (under the hood)
A Deployment treats its pods as **interchangeable: any replica is as good as any other, names are random, storage (if any) is shared or ephemeral, and the ReplicaSet replaces a dead pod with a fresh stranger. A StatefulSet** treats its pods as distinguishable members of an ordered set, and the StatefulSet controller upholds three guarantees:

1. Stable identity = name + DNS + storage, bound to an ordinal. Pod `kvstore-1` always has the name `kvstore-1`, the DNS record `kvstore-1.kvstore.<ns>.svc.cluster.local`, and the PVC `data-kvstore-1`. Delete the pod and the controller recreates *that same identity* - not a replacement, the same member returning. The PVC is the durable anchor: it outlives the pod, so the disk (and its data) re-attaches.

2. The headless Service is what creates per-pod DNS. A StatefulSet's `serviceName` must point at a headless Service (`clusterIP: None`). That Service tells the cluster's DNS to publish one A record **per pod**, named `<pod>.<svc>`. There is no VIP and no kube-proxy load-balancing - clients resolve a *specific* member directly. (We set `publishNotReadyAddresses: true` so peers can find each other during bootstrap, before redis answers `ping`.) Forget the headless Service and you keep ordinal names and PVCs but **lose stable DNS** - peers can't address each other.

3. **Ordered, at-most-one lifecycle. With `OrderedReady`, scale-up goes `0->1->2` (each waits for its predecessor's readiness) and scale-down goes in reverse `2->1->0`. The load-bearing guarantee is "at most one pod per ordinal at a time"**: the controller will **not** create a replacement `kvstore-1` until the old `kvstore-1` is *fully gone*. For clustered data systems this is correctness, not cosmetics - two pods sharing identity `1` (and trying to mount the same RWO disk, or claiming the same cluster slot) is split-brain.

Two design choices follow directly:
- **Scale-down retains PVCs on purpose.** Removing a replica is assumed to be reversible; the data must not vanish because you scaled to save cost. So the disks stay, ready to re-attach when you scale back. (Newer Kubernetes adds `persistentVolumeClaimRetentionPolicy` to opt into deletion - off by default.)
- Ordinality enables sequenced operations. Because creation is ordered and addressing is stable, an app can rely on "`-0` is the bootstrap/seed node," then `-1`/`-2` join it by DNS. Kubernetes gives you *order and identity*; the **app** uses them to form a cluster.

The crucial boundary: Kubernetes gives identity and storage, not consensus or HA. Three pods with stable names and disks are not a highly-available database until the *application* implements replication, leader election, and quorum. A StatefulSet is the substrate; the cluster logic lives in redis/etcd/Kafka/etc., not in the StatefulSet.

## Dev notes
- **Peer discovery via stable DNS.** Clustered apps bootstrap by addressing known members: `kvstore-0.kvstore` is the seed; `kvstore-1`/`-2` join it. You hard-code *names*, never IPs - the names are stable, the IPs aren't.
- Clustering/replication logic lives in the app. The StatefulSet won't replicate your data or pick a primary. If you need redis replication you run `redis-server --replicaof kvstore-0.kvstore 6379` (or Sentinel/Cluster); the StatefulSet only guarantees `kvstore-0` keeps being `kvstore-0`.
- `volumeClaimTemplates`, not a single PVC. Each ordinal gets its own claim. Mounting one shared RWO PVC across replicas is impossible (RWO = one node) and is the wrong model anyway - each member owns its shard/copy.

## DevOps / Platform notes
- Operating clustered data systems is hard - prefer managed. Backups, failover, upgrades, and quorum for a self-run database on a StatefulSet are real, paged-at-3am work. Unless you have a strong reason (cost at scale, data residency, an app with no managed offering), use RDS/Cloud SQL/ElastiCache/managed Redis/MSK and keep the StatefulSet for things that genuinely need in-cluster identity.
- **Backups are per-pod-volume.** There's no single disk to snapshot; you back up `data-kvstore-0..n` (volume snapshots) *and* coordinate with the app's consistency model (quiesce or snapshot a replica).
- **Upgrades use partitioned rollout.** `partition: N` updates only ordinals `≥ N`. Canary the highest ordinal, validate, then lower the partition step by step. This is your safe, reversible upgrade lever for stateful fleets - and `kubectl rollout` works on StatefulSets too.

## Architect notes (trade-offs)
- StatefulSet vs external managed datastore. A StatefulSet buys you identity + pinned storage + ordering *inside* the cluster (one network, one RBAC/NetworkPolicy domain, no egress, no separate bill line). A managed datastore (RDS/ElastiCache/Atlas) buys you HA, backups, patching, and failover *as a product* - at the price of being outside the cluster boundary. Choose StatefulSet when in-cluster locality/identity matters and you (or an operator) own the data plane; choose managed when you want the database to be someone else's pager.
- A StatefulSet is not HA or consensus for free. It is a *naming and storage* primitive. HA comes from the app running ≥3 members with replication and quorum - and from spreading those members across AZs (which zonal disks complicate, see below).

## SRE notes (failure modes, SLOs, toil)
- "At most one" is a correctness guarantee, not a convenience. The controller refusing to create a replacement `kvstore-1` until the old one is gone is what prevents two processes from claiming one identity / one disk - i.e. split-brain. This is *why* a stuck-`Terminating` `kvstore-1` (finalizer, lost node) **blocks** its own replacement: the controller is honoring the guarantee. Force-deleting it to "unstick" the StatefulSet can cause exactly the double-run you were protected from - do it only when you've confirmed the old pod is truly dead (KIA covers force-deletion caveats).
- **Ordered failover & bootstrap.** Order lets you sequence operations (seed primary first); it also means a wedged low ordinal stalls everything above it. Watch ordinal-0 health.
- Quorum & split-brain are the app's problem. Kubernetes gives identity; the app must tolerate a member being briefly absent (reschedule) and must not lose quorum. Run an odd number ≥3 for quorum-based systems.
- **Backup/restore per volume.** Your DR runbook restores `data-kvstore-K` snapshots and lets the app re-sync. Test restores.
- **Orphaned PVCs are toil + cost.** Scale-down and StatefulSet deletion leave disks behind by design. The recurring SRE chore is reconciling "PVCs with no owning StatefulSet" - stale data and a silent bill. (Lab Break-it C is this trap in miniature.)

## AI/ML notes (LLM/ML serving mapping - conceptual)
- Sharded model servers / distributed KV cache. When a model is too big for one replica, you shard it: tensor-parallel ranks, or a sharded vector index. Each shard needs a **stable identity** (rank 0 vs rank 1 are *not* interchangeable) and **pinned storage** (shard-0 must reload *its* slice of weights/index, not a random one). A StatefulSet maps cleanly: `shard-0.svc`, `shard-1.svc`, ... addressable by ordinal, each with its own PVC holding its shard.
- **Why not a Deployment.** A Deployment would hand a restarted rank a random name and (with the wrong volume) the wrong or empty shard - breaking the parallelism layout. The StatefulSet's "same ordinal -> same disk -> same shard" is exactly the invariant distributed inference needs.
- **Bootstrap ordering** mirrors clustered DBs: a coordinator/rank-0 comes up first; other ranks discover it by stable DNS and join the collective. (This is conceptual mapping - no GPU is used in this lab.)

## Pitfalls
- **Expecting HA for free. You get identity + storage + ordering, not** consensus. Three named pods aren't a cluster until the app makes them one.
- **Forgetting the headless Service.** Without it (or with a wrong `serviceName`) you lose per-pod DNS - peers can't address each other and clustering silently fails.
- PVCs lingering after scale-down/delete. By-design data safety becomes a cost-and-stale-data trap. Reconcile orphaned PVCs; delete them explicitly when retiring a StatefulSet.
- Reaching for a StatefulSet when a Deployment + external DB is simpler. If your app is stateless and just *talks to* a database, it's a Deployment. Use a StatefulSet only when the *pods themselves* need stable identity/storage.
- **Ignoring zonal storage topology.** RWO cloud disks are zonal; each ordinal's pod is pinned to its disk's AZ on reschedule. Spread members across AZs deliberately, and don't assume a pod can move freely.

## Further reading
- **KP "Stateful Service"** - the pattern, its forces, and when (not) to use it.
- **KIA ch10** - StatefulSets in depth: ordinality, headless Service, `volumeClaimTemplates`, ordered/partitioned updates, and the force-deletion caveats.
- Kubernetes docs: *StatefulSet Basics*, *StatefulSet update strategies* (`partition`), and `persistentVolumeClaimRetentionPolicy`.
