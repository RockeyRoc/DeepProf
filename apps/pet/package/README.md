# DeepProf Pet Package

`pet.json` 是桌宠包的唯一元数据入口，`spritesheet.webp` 是 DeepProf 娘的动作图集。
挂件不拥有 Runtime、Provider、Memory 或 API Key；它只接收事件投影并发送
`pet.interact` / `voice.transcript`。

动作行遵循设计文档 §19.8 的 8 列 × 9 行 v1 约定。开发期如果未安装美术资源，
工作台会显示一个无状态占位图，但不会改变 `PetDirector` 的事件语义。
