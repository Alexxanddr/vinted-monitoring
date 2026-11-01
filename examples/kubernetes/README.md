# Generic Kubernetes example

These are reviewable templates, not a completed installation. Choose and build an image first, replace the registry marker, and choose a block storage class in the PVC. Create the three private Secret fields from `secret.yaml.example` using a secret manager/SOPS; the placeholder Secret is intentionally excluded from kustomization. Adapt namespace and VM_ALLOWED_HOSTS together. No personal infrastructure or credential values belong here.

Use one replica and Recreate on a single RWO filesystem with working POSIX locks. An RWO volume alone does not prevent two pods on the same node; the process lock is also required. Do not scale for availability. A local block volume or suitable CSI filesystem is expected; NFS is unsupported. PVC deletion and volume reclaim policy are administrator responsibilities: choose Retain and backup policies before deployment.

Render locally with `kustomize build examples/kubernetes`. These templates do not require an operator, external database, RBAC permissions or ingress. Add an environment-specific NetworkPolicy permitting MCPHub ingress, DNS resolution and HTTPS egress to Vinted/Telegram (standard NetworkPolicy cannot select external hosts by DNS). Use trusted transport/TLS when bearer headers cross untrusted networks. Configure external stale-scan/backlog alerting separately from the process probes.

Apply only through your authorized deployment workflow. The example deliberately does not publish a host or assume Flux, Longhorn, a registry account or a cluster context. See [operations](../../docs/operations.md) for backup/restore and [configuration](../../docs/configuration.md) for MCPHub registration and checks. Resource requests are initial estimates, not measured capacity guarantees.
