# Contributing

This is a learning repo first. The bar is "is it correct and does it teach the thing well", not
"is it exhaustive".

## Ground rules for labs

If you add or change a lab, keep the three-file shape:

- `lab.md` withholds the commands. State what to achieve and what to prove, not the exact `kubectl`.
- `solution.md` has the worked commands plus the output you should expect.
- `lecture.md` explains the mechanism and the failure modes.

Manifests should also:

- Run in a per-lab namespace (`lab-NN-<slug>`), created in Setup and deleted in Cleanup. Never `default`.
- Pin image tags (no `:latest`) and set `resources.requests` so a lab is a good cluster citizen.
- Stay CPU-only. No `nvidia.com/gpu`; no GPU node pool. GPU/ML content belongs in the lecture AI/ML lens.
- Flag any cost-bearing resource (LoadBalancer, EBS/EFS, NAT) and tear it down in Cleanup.

## Before you push

```bash
make validate      # YAML parse + shape check over every manifest
make shellcheck    # if you touched serve.sh
```

CI runs the same checks. If you have `kubeconform` installed, `make kubeconform` does deeper schema
validation (CRD-backed kinds are skipped since they need the CRD installed).

## EKS vs OVH

Every cloud-specific step is written for both EKS and OVH. The OVH paths are the most likely to drift -
if you run the labs there and something is off (StorageClass, LoadBalancer annotations, identity), a
fix is especially welcome.
