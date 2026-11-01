#!/usr/bin/env bash
cd "$(dirname "$0")/.."
K="kubectl --context kind-ft-aitu"
$K apply -f k8s/security/00-namespaces.yaml
$K -n vault create configmap vault-scripts --from-file=security/vault --dry-run=client -o yaml | $K apply -f -
$K -n aitu-sim delete cronjob db-backup --ignore-not-found
$K apply -f k8s/security/20-vault.yaml
$K apply -f k8s/security/10-networkpolicies.yaml
$K -n vault rollout status deploy/vault-sealer --timeout=120s
