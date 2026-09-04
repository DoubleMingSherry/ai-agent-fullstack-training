# 任务级 Spec

## 目标
实现一个Agent loop的demo

## 业务约束
- 根据用户输入调用deepseek-v4-flash模型的API判断是否需要调用工具类，返回结构化数据，提供给后续loop使用
- 工具类包含两个（不用调用具体的tool，只要写模拟工具返回固定的信息即可）：1.查询气象台获取某个城市当天的天气 2.查询当前日期 
- 输出每一步的执行结果，包括调用了哪个工具，输入输出分别是什么

## 需要修改
- 增加一个单一的python类，目标路径：C:\baidunetdiskdownload\STUDY\Agent\repository\ai-agent-fullstack-training\practice\1_spec.py
- 不要改动其他代码

## 验收标准
- 当用户输入“今天是星期几？北京的天气如何？”，根据实际情况返回：“今天星期六，北京天气晴朗，气温24~30度。”
- git diff 不修改此次新增代码以外的改动