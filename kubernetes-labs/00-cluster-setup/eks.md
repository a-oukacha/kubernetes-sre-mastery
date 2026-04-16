# Cluster setup - Amazon EKS (primary target)

Goal: a small **3-node, multi-AZ EKS cluster with node-pool autoscaling and the add-ons every lab assumes. Cost is real but modest while running; delete the cluster when you stop** (`eksctl delete cluster`).

> Versions move fast. Pin a recent Kubernetes version and current chart versions; the commands below are the stable shape, not frozen pins. When in doubt, check the chart/add-on docs (the AWS Load Balancer Controller and Karpenter especially change syntax across releases).

---

## 0. Prerequisites (local)

- `awscli` v2, authenticated (`aws sts get-caller-identity` works).
- `eksctl`, `kubectl`, `helm` (see [`tooling.md`](tooling.md)).
- An AWS account where you may create VPC/EKS/EC2/ELB/EBS resources.

Set shared env:

```bash
export AWS_REGION=eu-west-1
export CLUSTER=k8s-sre-course
```

---

## 1. Provision the cluster

Use the cluster config in `manifests/eks-cluster.yaml` (3× `t3.large` across 3 AZs, managed node group min=2/max=5, OIDC enabled for IRSA):

```bash
eksctl create cluster -f manifests/eks-cluster.yaml
kubectl get nodes -o wide          # expect 3 Ready nodes in 3 AZs
```

`eksctl` writes your kubeconfig automatically. Confirm the context:

```bash
kubectl config current-context     # ...@k8s-sre-course.eu-west-1.eksctl.io
```

OIDC (needed for IRSA in lab 19) is enabled by the config file's `iam.withOIDC: true`. If you provisioned another way:

```bash
eksctl utils associate-iam-oidc-provider --cluster $CLUSTER --approve
```

---

## 2. Add-ons

### 2.1 EBS CSI -> default `gp3` StorageClass (labs 09, 10)
```bash
eksctl create addon --name aws-ebs-csi-driver --cluster $CLUSTER --force
kubectl apply -f manifests/eks-gp3-storageclass.yaml   # gp3, default, WaitForFirstConsumer
```
This gives **RWO** dynamic volumes. (RWX comes from EFS, below.)

### 2.2 EFS CSI -> RWX StorageClass (lab 09 RWX demo)
```bash
helm repo add aws-efs-csi-driver https://kubernetes-sigs.github.io/aws-efs-csi-driver/
helm upgrade --install aws-efs-csi-driver aws-efs-csi-driver/aws-efs-csi-driver -n kube-system
# Create an EFS filesystem + mount targets in the cluster's VPC/subnets, then:
kubectl apply -f manifests/eks-efs-storageclass.yaml   # set fileSystemId
```
> EFS + mount targets cost money. Only create for lab 09's RWX section; delete after.

### 2.3 metrics-server (labs 05, 18)
```bash
eksctl create addon --name metrics-server --cluster $CLUSTER --force
kubectl top nodes      # should return CPU/mem after ~30s
```

### 2.4 AWS Load Balancer Controller (labs 06, 07 - NLB/ALB)
```bash
eksctl create iamserviceaccount --cluster $CLUSTER -n kube-system \
  --name aws-load-balancer-controller \
  --attach-policy-arn arn:aws:iam::aws:policy/AWSLoadBalancerControllerIAMPolicy \
  --approve --role-name AmazonEKSLoadBalancerControllerRole
helm repo add eks https://aws.github.io/eks-charts
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system --set clusterName=$CLUSTER \
  --set serviceAccount.create=false --set serviceAccount.name=aws-load-balancer-controller
```
> If `AWSLoadBalancerControllerIAMPolicy` doesn't exist yet, create it from the controller's published policy JSON first.

### 2.5 ingress-nginx (lab 07 - preferred over ALB so manifests match OVH)
```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace \
  --set controller.service.type=LoadBalancer
kubectl -n ingress-nginx get svc ingress-nginx-controller   # EXTERNAL-IP = an NLB hostname
```

### 2.6 cert-manager (lab 07 TLS)
```bash
helm repo add jetstack https://charts.jetstack.io
helm upgrade --install cert-manager jetstack/cert-manager \
  -n cert-manager --create-namespace --set crds.enabled=true
```

### 2.7 Cluster Autoscaler (lab 18) - default for OVH parity
```bash
eksctl create iamserviceaccount --cluster $CLUSTER -n kube-system --name cluster-autoscaler \
  --attach-policy-arn arn:aws:iam::aws:policy/AutoScalingFullAccess --approve   # tighten in prod
helm repo add autoscaler https://kubernetes.github.io/autoscaler
helm upgrade --install cluster-autoscaler autoscaler/cluster-autoscaler \
  -n kube-system --set autoDiscovery.clusterName=$CLUSTER --set awsRegion=$AWS_REGION \
  --set rbac.serviceAccount.create=false --set rbac.serviceAccount.name=cluster-autoscaler
```
> **Karpenter** is the modern AWS alternative (provisions right-sized nodes directly, no node groups). Lab 18's lecture compares both; Cluster Autoscaler is the default here because OVH has the equivalent and Karpenter does not.

### 2.8 kube-prometheus-stack (labs 13, 20)
```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace
```

### 2.9 NetworkPolicy enforcement (lab 08)
EKS's VPC CNI can enforce NetworkPolicy (recent versions): set `enableNetworkPolicy=true` on the `vpc-cni` add-on, **or** install Calico. Lab 08 details both.

---

## 3. Identity for lab 19 (IRSA / Pod Identity)
IRSA maps a Kubernetes ServiceAccount to an IAM role via the OIDC provider (enabled in step 1). Lab 19 walks the full `eksctl create iamserviceaccount` flow to grant a pod scoped S3 access **without static keys**. **EKS Pod Identity** is the newer alternative (an add-on + association); lab 19 mentions it.

---

## 4. Teardown
```bash
# Per lab: each lab.md Cleanup deletes its namespace + any LB/volume it created.
# End of day - delete everything:
helm uninstall ingress-nginx -n ingress-nginx           # releases the NLB
eksctl delete cluster -f manifests/eks-cluster.yaml     # or: --name $CLUSTER
```
> Deleting the cluster does **not always delete `LoadBalancer` Services' ELBs or EFS filesystems if the controller was already gone. Delete `type=LoadBalancer` Services and EFS filesystems before** deleting the cluster.
