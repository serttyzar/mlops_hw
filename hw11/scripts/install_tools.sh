#!/bin/sh
# Install only to this homework's directory. Published checksums are mandatory.
set -eu
cd "$(dirname "$0")/.."
mkdir -p .tools
case "$(uname -s)" in Darwin) os=darwin;; Linux) os=linux;; *) exit 1;; esac
case "$(uname -m)" in arm64|aarch64) arch=arm64;; x86_64) arch=amd64;; *) exit 1;; esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fetch() { curl --fail --location --retry 2 --connect-timeout 10 --max-time 180 "$1" -o "$2"; }
verify() {
  expected=$(awk '{print $1}' "$2")
  actual=$(shasum -a 256 "$1" | awk '{print $1}')
  test "$actual" = "$expected" || { echo "Checksum failed: $1" >&2; exit 1; }
}
fetch "https://dl.k8s.io/release/v1.32.2/bin/$os/$arch/kubectl" "$work/kubectl"
fetch "https://dl.k8s.io/release/v1.32.2/bin/$os/$arch/kubectl.sha256" "$work/kubectl.sha256"
verify "$work/kubectl" "$work/kubectl.sha256"
fetch "https://kind.sigs.k8s.io/dl/v0.27.0/kind-$os-$arch" "$work/kind"
fetch "https://kind.sigs.k8s.io/dl/v0.27.0/kind-$os-$arch.sha256sum" "$work/kind.sha256"
verify "$work/kind" "$work/kind.sha256"
fetch "https://get.helm.sh/helm-v3.16.4-$os-$arch.tar.gz" "$work/helm.tar.gz"
fetch "https://get.helm.sh/helm-v3.16.4-$os-$arch.tar.gz.sha256sum" "$work/helm.sha256"
verify "$work/helm.tar.gz" "$work/helm.sha256"
tar -xzf "$work/helm.tar.gz" -C "$work"
cp "$work/kubectl" .tools/kubectl
cp "$work/kind" .tools/kind
cp "$work/$os-$arch/helm" .tools/helm
chmod +x .tools/kubectl .tools/kind .tools/helm
printf '%s\n' 'Tools installed in .tools (kubectl 1.32.2, kind 0.27.0, helm 3.16.4).'
