"""ObjectStorePort 企业档：S3/OSS（ADR 装配表可换档）。放行项⑯ 真实现（M4-bu）。

G5/ADR-005：`filesystem` 为执行档默认对象库；`s3` 是企业档，同 `ObjectStorePort` 契约、由装配表 `c.objectstore` 切换。
M4-aq 收口此前 `c.objectstore` 组合根零读的静默错配；M4-bu 把占位升为**真 boto3 适配**——`put`/`get`/`exists`
经 `put_object`/`get_object`/`head_object` 落真实 S3 语义。

懒导入纪律：组合根**无条件** `from ...objectstore_s3 import S3ObjectStore`（顶层），故本模块顶层**不得** import
boto3——否则未装 boto3 的单机默认档连装配 import 都会崩。故 boto3 只在构造客户端时函数内 import，缺驱动即
`ProviderNotConfigured` fail-loud、绝不静默回落 filesystem（同 M4-ap langgraph）。凭证走 boto3 默认 env 链（T1），
本文件不碰字面密钥。`client=` 注入位供 botocore `Stubber` 离线契约测试（无网络、无真实桶）——真实桶往返须
endpoint/凭证/服务端就位，属企业放行档，本环不伪造。
"""

from __future__ import annotations

from typing import Any

from src.platform.providers.errors import ProviderNotConfigured


def _require_client(endpoint_url: str | None, region: str | None) -> Any:
    try:
        import boto3  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 取决于环境是否装 extra
        raise ProviderNotConfigured(
            "s3 编排档需 pip install boto3（放行项⑯ 已备真实现，Apache-2.0；缺驱动即 fail-loud 不静默回落 fs）"
        ) from exc
    return boto3.client("s3", endpoint_url=endpoint_url, region_name=region)


def _is_not_found(exc: Exception) -> bool:
    """只把明确的 S3 '对象不存在'（head/get 404 或 NoSuchKey）判为 not-found，其余原样上抛（NFR-8 不静默吞异常）。"""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error_code = str(response.get("Error", {}).get("Code", ""))
    http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return error_code in {"404", "NoSuchKey", "NotFoundError"} or http_status == 404


class S3ObjectStore:
    def __init__(
        self, bucket: str | None, *, endpoint_url: str | None = None, region: str | None = None, client: Any = None
    ) -> None:
        if not bucket:
            raise ProviderNotConfigured("s3 需对象桶配置（env 注入 s3_bucket，T1/企业放行项）")
        self._bucket = bucket
        # client=None 时才真构造（需 boto3）；注入 client（Stubber 测试/既有会话）则不触懒导入。
        self._client = client if client is not None else _require_client(endpoint_url, region)

    def put(self, key: str, data: bytes) -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        return key

    def get(self, key: str) -> bytes:
        return bytes(self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read())

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # botocore ClientError 系懒导入不可在顶层 import，按结构判定 not-found
            if _is_not_found(exc):
                return False
            raise
        return True
