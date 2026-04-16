# Cluster setup - OVHcloud Managed Kubernetes (MKS, secondary target)

Every lab runs on OVH MKS too. This file gives the OVH equivalents of each EKS add-on. Where OVH lacks a feature (RWX storage, IAM-per-pod), the gap is called out - those gaps are themselves teaching points (labs 09 and 19).

> **Cost:** the cluster nodes and any **OVH Load Balancer** (public IP) are billed. Tear down LBs and the cluster when idle.

---

## 0. Prerequisites (local)
- An OVHcloud account with a Public Cloud project.
- `kubectl`, `helm` (see [`tooling.md`](tooling.md)). Optionally the `ovhai`/OpenStack CLIs or Terraform (`ovh` + `openstack` providers).

---

## 1. Provision the cluster
Easiest path - **OVH Manager UI: Public Cloud -> Managed Kubernetes Service -> create cluster, pick a region with ≥3 zones, then add one node pool**:
- flavor ~`b2-7` (2 vCPU / 7 GB) ×3,
- **autoscaling enabled, min=2 / max=5** (OVH manages the cluster-autoscaler for the pool).

Download the kubeconfig from the cluster's page and point at it:
```bash
export KUBECONFIG=~/Downloads/kubeconfig-k8s-sre-course.yml
kubectl get nodes -o wide      # expect Ready nodes spread across zones
```
> Terraform alternative: `ovh_cloud_project_kube` + `ovh_cloud_project_kube_nodepool` with `autoscale = true`, `min_nodes`, `max_nodes`. Multi-AZ MKS spreads a pool's nodes across the region's zones.

---

## 2. Add-ons (OVH equivalents)

### 2.1 Storage - Cinder CSI (RWO) - labs 09, 10
MKS ships Cinder CSI with StorageClasses preinstalled:
```bash
kubectl get storageclass
# csi-cinder-classic        (HDD-backed, RWO)
# csi-cinder-high-speed     (SSD-backed, RWO)  <- use this where the lab wants a default
```
Use `csi-cinder-high-speed` wherever a lab says "default StorageClass / `gp3`". All are **RWO**.

**RWX caveat (lab 09):** OVH has **no native RWX block storage**. For lab 09's RWX section:
- deploy `nfs-subdir-external-provisioner` (Helm) backed by a node or an OVH NAS-HA share, **or**
- use **OVH NAS-HA** (separate product) mounted via NFS.
Document this as a real-world limitation - RWX is not free on every cloud.

### 2.2 metrics-server (labs 05, 18)
Usually preinstalled on MKS. Verify:
```bash
kubectl top nodes || \
  helm upgrade --install metrics-server metrics-server/metrics-server -n kube-system \
    --repo https://kubernetes-sigs.github.io/metrics-server/
```

### 2.3 LoadBalancer (labs 06, 07)
`Service type=LoadBalancer` provisions an **OVH Load Balancer** via the cloud-controller. Useful annotations live under `service.beta.kubernetes.io/ovh-loadbalancer-*`. The public IP is **billed** - tear down after the lab.

### 2.4 ingress-nginx + cert-manager (lab 07) - identical to EKS
The Helm charts are portable; the only difference is the LB underneath:
```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace --set controller.service.type=LoadBalancer
# cert-manager: same commands as eks.md §2.6
```

### 2.5 Autoscaler (lab 18)
The **node-pool autoscaler (min/max set at pool creation) is managed by OVH - there is no Karpenter equivalent**. Lab 18 uses the pool min/max; the lecture notes the difference vs EKS Cluster Autoscaler/Karpenter.

### 2.6 NetworkPolicy (lab 08)
MKS CNI is **Cilium** (recent) or Canal depending on cluster version. Verify NetworkPolicy is enforced (lab 08 has a test); if your version doesn't enforce, install Calico/Cilium. Cilium also unlocks L7 policy used in the lecture's "where to go next."

### 2.7 kube-prometheus-stack (labs 13, 20) - identical to EKS
Same Helm chart and commands as `eks.md §2.8`.

---

## 3. Identity for lab 19 - the gap
OVH has **no IAM-per-pod** (no IRSA equivalent). To give a pod scoped access to an OVH object-storage bucket or external API, lab 19 uses:
- **External Secrets Operator** against a vault (e.g. HashiCorp Vault, or OVH-hosted secrets), or
- **Sealed Secrets** (encrypt secrets into Git, decrypt in-cluster).

The lecture contrasts this with EKS IRSA explicitly - "cloud-native workload identity" is not universal.

---

## 4. Teardown
```bash
helm uninstall ingress-nginx -n ingress-nginx     # releases the OVH LB
# Delete any remaining type=LoadBalancer Services, then delete the cluster in the Manager UI
# (or `terraform destroy`).
```
