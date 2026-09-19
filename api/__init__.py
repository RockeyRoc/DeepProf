"""DeepProf API 接入层（DESIGNv0.4 §8：FastAPI + SSE；§16.1 责任人：刘俊鹏）。

职责与边界（§4.4）：
- 交互层（Electron/React）只与 API 通信，不直连 Tool / Memory / Provider；
- API 只做"请求校验 → 调用 RuntimeService → 转发事件/流式增量"，
  不承担教学策略判断（那是 Pedagogical Graph 的事）；
- 密钥只存在于服务端 Provider，API 响应永不回传密钥（§12 / §16.4）。

入口：
    - ``api.app:app``        供 uvicorn 启动
    - ``api.app.create_app()`` 供测试与嵌入（可注入 RuntimeService）
"""