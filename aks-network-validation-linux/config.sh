#!/usr/bin/env bash
# 공통 설정(source 전용). 모든 스크립트는 이 파일의 변수로만 이름·주소를 참조한다.
# 리소스 그룹은 AKSNETVAL_RG 환경변수로 바꿀 수 있다. az 는 AZ 환경변수로 대체 가능(테스트용).

_CFG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RG="${AKSNETVAL_RG:-rg-aksnetval-linux-krc}"
LOCATION="koreacentral"
VNET="vnet-aksnetval"
VNET_PREFIX="10.50.0.0/16"
SNET_AKS_APP_NAME="snet-aks-app";     SNET_AKS_APP_PREFIX="10.50.4.0/22"
SNET_INGRESS_NAME="snet-ingress";     SNET_INGRESS_PREFIX="10.50.1.0/27"
SNET_PE_NAME="snet-pe";               SNET_PE_PREFIX="10.50.2.0/24"
SNET_APPGW_SIM_NAME="snet-appgw-sim"; SNET_APPGW_SIM_PREFIX="10.50.3.0/27"
SNET_OTHER_NAME="snet-other";         SNET_OTHER_PREFIX="10.50.3.32/27"
SNET_MGMT_NAME="snet-mgmt";           SNET_MGMT_PREFIX="10.50.3.64/27"
NSG_AKS_APP="nsg-aks-app"
NSG_INGRESS="nsg-ingress"
# nsg-ingress 의 Deny 규칙(01-infra 생성, 03-verify TN4 확인)
NSG_INGRESS_DENY_RULE="deny-appgw-all"
IDENTITY="id-aksnetval"
INGRESS_IP="10.50.1.10"
POD_CIDR="192.168.0.0/16"
AKS="aks-aksnetval"
SYSTEM_VM_SIZE="Standard_D4s_v5"
USER_VM_SIZE="Standard_D2s_v5"
USER_POOLS=(userz1 userz2 userz3)
USER_MIN=1
USER_MAX=3
# koreacentral 에는 Standard_B2s 가 제공되지 않아 v2 로 대체
VM_SIZE="Standard_B2s_v2"
VM_APPGW="vm-appgw-sim"
VM_OTHER="vm-other"
VM_MGMT="vm-mgmt"
NAT_MGMT="nat-mgmt"
PIP_NAT_MGMT="pip-nat-mgmt"
BLOB_PE="pe-blob-aksnetval"
# app 네임스페이스 NetworkPolicy 가 /32 로 허용하므로 PE IP 를 고정한다(manifests/31-app-pe-egress.yaml 과 일치)
BLOB_PE_IP="10.50.2.4"
BLOB2_PE="pe-blob2-aksnetval"
BLOB2_PE_IP="10.50.2.5"
BLOB_DNS_ZONE="privatelink.blob.core.windows.net"
BLOB_DNS_LINK="link-vnet-aksnetval"
TAGS="purpose=aksnetval"
VM_ROOT="${VM_ROOT:-/opt/aksnetval}"
MANIFESTS_DIR="$_CFG_DIR/manifests"

# 구독 ID 는 Storage 이름 해시에만 쓰고 파일에 남기지 않는다
_sub="$("${AZ:-az}" account show --query id -o tsv 2>/dev/null | tr -d '\r[:space:]')" || _sub=""
if [[ -z "$_sub" ]]; then
  echo "az account show 실패: az login(관리 VM 은 az login --identity) 후 다시 실행하세요" >&2
  return 1 2>/dev/null || exit 1
fi
_hash="$(printf '%s' "$_sub/$RG" | sha256sum | cut -c1-6)"
STORAGE="staksnetval$_hash"
# bff-a 전용 PE 검증용 두 번째 Storage
STORAGE2="staksnetvalb$_hash"
unset _sub _hash

export RG LOCATION VNET AKS VM_MGMT VM_ROOT MANIFESTS_DIR STORAGE STORAGE2
