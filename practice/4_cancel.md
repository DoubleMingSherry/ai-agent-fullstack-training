# 协作式取消

## 目标
复用 3_adapter 的全部零件，并为事件流增加“协作式取消”。

## 业务约束
- 保留 practice/3_adapter.py 的全部零件，通过 importlib.import_module('3_adapter') 导入(文件名以数字开头，无法用普通 import 语句)，3_adapter.py 一个字不动
- 每消费N个事件调用一次注入的cancel_check()
- cancel_check() 模拟用户操作信号，采用可控方式：第M次检查返回 True（原因不由 cancel_check 返回，由包装器写入固定字符串，如“用户取消”）
- 如果cancel_check() 返回 True，则状态机进入cancelling状态，当前检查点之后不再产出任何model.*事件；保存数据（last_seq、已输出文本、原因）到内存中，其中last_seq为取消前最后一个已产出事件的seq；保存完成后，状态机进入 cancelled状态，run.cancelled
 事件本身消耗seq，其seq=last_seq+1
- 不允许状态机从running直接进入cancelled状态，如有则 raise异常
- 取消后继续喂chunk不再产出任何事件
- 同3_adapter 一样，采用模拟chunk流，不实际调用模型

## 需要修改
- 新增单文件: C:\baidunetdiskdownload\STUDY\Agent\repository\ai-agent-fullstack-training\practice\4_cancel.py
- 除以上外不改动任何代码

## 验收标准
- RunStateMachine：running → cancelling → cancelled；
  非法跳转（如 running → cancelled）必须 raise，不得静默
- 取消检查点：包装 3_adapter 的事件迭代器，每消费 3 个事件
  调用一次注入的 cancel_check() 回调（DI，和 chunk 迭代器同款思路）
- cancel_check() 第2次检查返回 True
- 测试用足够长的流，保证至少触发2次检查
- 触发取消后：状态机进入 cancelling → 产出且仅产出一个
  run.cancelled 事件（含 last_seq、已输出文本、原因）→ cancelled
- 取消后继续喂 chunk：产出零事件（含 run.cancelled 在内，不得重复产出；协作式生效）
- 全部断言离线通过；不 import openai；不发网络请求
- git diff 不含上述新增以外的改动
