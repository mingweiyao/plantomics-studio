"""模块 API 代理

主程序的 /modules/<module_id>/<path> 自动转发到模块自己的 Python 后端。
模块完全不知道自己被代理,它只是个独立的 HTTP 服务监听 127.0.0.1:<port>。

如果未来支持 R 转发,可以加 /modules/<id>/r/<path> 区分。
现在统一走 Python 后端,模块如有 R 后端,在 Python 端再代理一次。
"""
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter()


# 复用同一个 httpx client(连接池)
_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # trust_env=False 关键:不读环境里的 HTTP_PROXY / HTTPS_PROXY
        # 否则用户机器上设了代理(clash/v2ray 等),localhost 请求也会被送给代理
        _client = httpx.AsyncClient(timeout=120.0, trust_env=False)
    return _client


@router.api_route(
    "/{module_id}/{rest_of_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def proxy(module_id: str, rest_of_path: str, request: Request):
    registry = request.app.state.registry
    mod = registry.get(module_id)
    if not mod:
        raise HTTPException(404, f"模块未安装: {module_id}")
    if mod.status != "ready":
        raise HTTPException(
            503,
            f"模块状态: {mod.status}({mod.error or '未就绪'})。请稍候或重启。"
        )
    if not mod.py_port:
        raise HTTPException(
            501,
            f"模块 {module_id} 没有 Python 后端,无法代理"
        )
    
    target_url = f"http://127.0.0.1:{mod.py_port}/{rest_of_path}"
    
    # 透明转发
    method = request.method
    headers = dict(request.headers)
    # 删 host,httpx 会自己设
    headers.pop("host", None)
    body = await request.body()
    params = dict(request.query_params)
    
    client = await _get_client()
    try:
        resp = await client.request(
            method=method,
            url=target_url,
            headers=headers,
            content=body,
            params=params,
        )
    except httpx.ConnectError:
        # 模块进程可能挂了,标记为 error
        mod.status = "error"
        mod.error = f"无法连接到 127.0.0.1:{mod.py_port}"
        raise HTTPException(502, f"模块 {module_id} 后端不可达")
    except httpx.TimeoutException:
        raise HTTPException(504, f"模块 {module_id} 响应超时")
    
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )
