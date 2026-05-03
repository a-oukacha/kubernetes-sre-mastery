# Lab 10 - StatefulSets · **Solution**
**Patterns:** Stateful Service **Source:** KIA 10; KP "Stateful Service" **Est:** 55 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
- A reachable cluster with a **default RWO StorageClass** (`kubectl get sc` shows one marked `(default)`). See `../00-cluster-setup/`.

## Setup
```bash
kubectl create namespace lab-10-statefulsets
kubens lab-10-statefulsets          # or add -n lab-10-statefulsets to every command

kubectl apply -f manifests/svc-headless.yaml    # REQUIRED headless Service (DNS)
kubectl apply -f manifests/statefulset.yaml      # 3x redis, one PVC per ordinal
kubectl apply -f manifests/client.yaml           # busybox client for nslookup
kubectl wait --for=condition=Ready pod/client --timeout=60s
```
> **COST GUARD.** `volumeClaimTemplates` provisions **one real cloud disk per replica** (3 replicas -> **3 billed disks**). These PVCs are **not** deleted when you scale down, and **not** deleted when you delete the StatefulSet - Cleanup deletes them explicitly. Don't walk away without running Cleanup.

**Predict (0):** You applied a StatefulSet with `replicas: 3` and `podManagementPolicy: OrderedReady`. Will all three pods appear at once, or one at a time? In what name order? Watch with `kubectl get pods -l app.kubernetes.io/name=kvstore -w` (Ctrl-C after all 3 are Running).

---

## Steps

### 1. Watch ORDERED creation: 0 -> 1 -> 2
```bash
kubectl get pods -l app.kubernetes.io/name=kvstore -w
# Ctrl-C once kvstore-0, kvstore-1, kvstore-2 are all 1/1 Running
kubectl get pods -l app.kubernetes.io/name=kvstore
```
**Observe (1): The pod names are `kvstore-0`, `kvstore-1`, `kvstore-2` - not** random suffixes. With `OrderedReady`, `kvstore-1` was not created until `kvstore-0` was `Ready`, and `kvstore-2` waited on `kvstore-1`. Note which pod reached `Running` first.

### 2. Each ordinal gets its OWN PVC
```bash
kubectl get pvc -l app.kubernetes.io/name=kvstore
kubectl get pvc -o custom-columns=PVC:.metadata.name,STATUS:.status.phase,VOLUME:.spec.volumeName
```
**Observe (2):** Exactly **three** PVCs exist: `data-kvstore-0`, `data-kvstore-1`, `data-kvstore-2` - one per ordinal, named `<template>-<sts>-<ordinal>`. Each is `Bound` to its own PersistentVolume (a distinct cloud disk).

### 3. Resolve a SINGLE pod by stable DNS
```bash
kubectl exec client -- nslookup kvstore-0.kvstore
kubectl exec client -- nslookup kvstore-2.kvstore
# compare to the headless Service name itself (returns ALL pod IPs):
kubectl exec client -- nslookup kvstore
kubectl get pods -l app.kubernetes.io/name=kvstore -o wide
```
**Predict (3):** `kvstore-0.kvstore` is a per-pod DNS name. How many `Address:` lines do you expect it to return, and whose IP is it? How many does the bare `kvstore` (the headless Service) return? Confirm `kvstore-0.kvstore`'s address matches `kvstore-0`'s IP in `get pods -o wide`.

### 4. Write DISTINCT data into each pod's volume
Each pod runs redis with its data dir on its own PVC. Stamp a unique key per pod:
```bash
for i in 0 1 2; do
  kubectl exec kvstore-$i -- redis-cli set owner "kvstore-$i" >/dev/null
  kubectl exec kvstore-$i -- redis-cli set seq "$i" >/dev/null
done
# read it back + show the on-disk identity file each pod stamped on first boot:
for i in 0 1 2; do
  echo "== kvstore-$i =="
  kubectl exec kvstore-$i -- redis-cli get owner
  kubectl exec kvstore-$i -- cat /data/identity.txt
done
```
**Prove it (4):** Each pod returns its *own* `owner` value and its *own* `identity.txt` line (mentioning its own hostname). The data is per-pod because each pod has its own PVC - there is no shared storage here.

### 5. Scale DOWN to 1 - watch reverse order, PVCs REMAIN
```bash
kubectl get pods -l app.kubernetes.io/name=kvstore -w &      # leave watching
kubectl scale statefulset/kvstore --replicas=1
sleep 8; kill %1 2>/dev/null
kubectl get pods -l app.kubernetes.io/name=kvstore           # only kvstore-0 left
kubectl get pvc -l app.kubernetes.io/name=kvstore            # STILL THREE PVCs
```
**Predict (5):** Scaling 3->1 removes pods in which order? After scale-down, how many PVCs remain - 1 or 3? Check `get pvc` and explain what happened to the disks for ordinals 1 and 2.

### 6. Scale back to 3 - original PVCs + data RE-ATTACH
```bash
kubectl scale statefulset/kvstore --replicas=3
kubectl rollout status statefulset/kvstore --timeout=120s
# kvstore-1 and kvstore-2 come back - do they have their OLD data?
for i in 1 2; do
  echo "== kvstore-$i =="
  kubectl exec kvstore-$i -- redis-cli get owner
  kubectl exec kvstore-$i -- cat /data/identity.txt
done
```
**Prove it (6): `kvstore-1` returns `owner = kvstore-1` and its original** `identity.txt` (same first-boot timestamp), because it re-bound `data-kvstore-1` - the exact disk it had before. Identity *and* data survived the scale-down/up cycle.

### 7. Partitioned rolling update - canary the HIGHEST ordinal first
`statefulset-partition.yaml` adds env `REDIS_BUILD=canary` and sets `partition: 2`.
```bash
kubectl apply -f manifests/statefulset-partition.yaml
kubectl rollout status statefulset/kvstore --timeout=120s
# which pods now carry the new env var?
for i in 0 1 2; do
  echo -n "kvstore-$i REDIS_BUILD="; \
  kubectl get pod kvstore-$i -o jsonpath='{.spec.containers[0].env[?(@.name=="REDIS_BUILD")].value}'; echo
done
```
**Predict (7): With `partition: 2`, which ordinals get the new `REDIS_BUILD=canary` env, and which stay on the old spec? Now promote** the rollout by lowering the partition and re-checking:
```bash
kubectl patch statefulset/kvstore --type=json \
  -p='[{"op":"replace","path":"/spec/updateStrategy/rollingUpdate/partition","value":0}]'
kubectl rollout status statefulset/kvstore --timeout=120s
for i in 0 1 2; do
  echo -n "kvstore-$i REDIS_BUILD="; \
  kubectl get pod kvstore-$i -o jsonpath='{.spec.containers[0].env[?(@.name=="REDIS_BUILD")].value}'; echo
done
```
Note the update order when partition drops to 0 (which ordinal updates last?).

---

## Verify
```bash
# exactly one PVC per ordinal:
kubectl get pvc -l app.kubernetes.io/name=kvstore -o name | wc -l        # -> 3
kubectl get pvc -l app.kubernetes.io/name=kvstore -o name                # data-kvstore-0/1/2

# per-pod DNS resolves to that specific pod's IP:
kubectl exec client -- nslookup kvstore-1.kvstore | grep '^Address' | tail -1
kubectl get pod kvstore-1 -o jsonpath='{.status.podIP}'; echo            # same IP

# delete + recreate kvstore-1: SAME name, SAME PVC, SAME data:
kubectl delete pod kvstore-1
kubectl wait --for=condition=Ready pod/kvstore-1 --timeout=120s
kubectl exec kvstore-1 -- redis-cli get owner                            # -> kvstore-1
kubectl get pod kvstore-1 -o jsonpath='{.spec.volumes[?(@.name=="data")].persistentVolumeClaim.claimName}'; echo  # data-kvstore-1
```
yes Success = **3 PVCs, one per ordinal; `kvstore-1.kvstore` resolves to `kvstore-1`'s IP; after delete+recreate, `kvstore-1` has the same** name, the **same** PVC (`data-kvstore-1`), and the **same** data (`owner = kvstore-1`).

---

## Break it - identity & storage survive; a Deployment's don't

### Break-it A - delete a StatefulSet pod -> SAME identity + SAME disk
```bash
kubectl get pod kvstore-1 -o jsonpath='{.metadata.uid}{"\n"}'   # note the UID
kubectl delete pod kvstore-1
kubectl get pods -l app.kubernetes.io/name=kvstore -w           # Ctrl-C once kvstore-1 is Running again
kubectl exec kvstore-1 -- redis-cli get owner                    # still kvstore-1
kubectl exec kvstore-1 -- cat /data/identity.txt                 # ORIGINAL first-boot line
```
**Predict (B1): The replacement is a brand-new pod (new UID). What name** will it get, and will it have the data you wrote in Step 4? Why can the controller reuse `data-kvstore-1`?

### Break-it B - the same app as a Deployment: random name, EMPTY volume
```bash
kubectl apply -f manifests/deploy-contrast.yaml
kubectl rollout status deployment/kvstore-deploy --timeout=60s
kubectl get pods -l app.kubernetes.io/name=kvstore-deploy        # note the RANDOM suffixes
POD=$(kubectl get pod -l app.kubernetes.io/name=kvstore-deploy -o jsonpath='{.items[0].metadata.name}')
kubectl exec "$POD" -- redis-cli set owner "$POD" >/dev/null
kubectl delete pod "$POD"
kubectl rollout status deployment/kvstore-deploy --timeout=60s
NEW=$(kubectl get pod -l app.kubernetes.io/name=kvstore-deploy -o jsonpath='{.items[0].metadata.name}')
echo "old=$POD new=$NEW"
kubectl exec "$NEW" -- redis-cli get owner                       # what's there now?
```
**Predict (B2): Compare the Deployment's replacement to the StatefulSet's. Does `$NEW` have the same name** as `$POD`? Does it have the data you set on `$POD` (the Deployment uses an `emptyDir`)? State the two guarantees the StatefulSet gave that the Deployment did not.

### Break-it C - scale to 0, then back: data returns; deletion orphans PVCs
```bash
kubectl scale statefulset/kvstore --replicas=0
kubectl get pods -l app.kubernetes.io/name=kvstore               # none
kubectl get pvc -l app.kubernetes.io/name=kvstore                # STILL THREE (data safe)
kubectl scale statefulset/kvstore --replicas=3
kubectl rollout status statefulset/kvstore --timeout=120s
kubectl exec kvstore-2 -- redis-cli get owner                    # -> kvstore-2 (data came back)

# now the cost trap - delete the StatefulSet but NOT the PVCs:
kubectl delete statefulset/kvstore
kubectl get pvc -l app.kubernetes.io/name=kvstore                # PVCs (and disks) STILL EXIST
```
**Predict (B3):** After `kubectl delete statefulset/kvstore`, how many PVCs remain? Are the underlying cloud disks still provisioned (and billed)? What single command from Cleanup actually frees them?

---

## Cleanup
```bash
# StatefulSet PVCs are NOT garbage-collected - delete them EXPLICITLY or the cloud
# disks linger and keep billing. Deleting the namespace below also deletes the PVCs
# in it, which releases the disks (default reclaimPolicy: Delete).
kubectl delete -f manifests/ --ignore-not-found
kubectl delete pvc -l app.kubernetes.io/name=kvstore --ignore-not-found

# Then the namespace removes everything else AND any remaining PVCs (releasing disks):
kubectl delete namespace lab-10-statefulsets

# Confirm no orphaned disks remain billing:
kubectl get pvc -A | grep kvstore        # should return nothing
```
> The headless Service and client cost nothing. **The per-ordinal PVCs are the cost** - three RWO cloud disks. They survive scale-down and StatefulSet deletion by design (data safety); only deleting the PVCs/namespace releases them.

### EKS
`volumeClaimTemplates` uses the default RWO StorageClass **gp3** (EBS CSI). EBS volumes are **zonal: each ordinal's PVC pins that ordinal's pod to the AZ** where its disk lives, so a rescheduled `kvstore-1` must land in the same AZ as `data-kvstore-1`. Multi-AZ node groups still satisfy this because the scheduler honors the volume's topology.

### OVH
Default RWO StorageClass is **csi-cinder-high-speed (Cinder CSI). Cinder volumes are likewise zonal**: each ordinal's disk pins its pod to that zone on reschedule. Confirm a default SC exists (`kubectl get sc`); if none is marked `(default)`, set `csi-cinder-high-speed` as default first.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
