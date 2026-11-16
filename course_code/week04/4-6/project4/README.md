# project3：Codebase Agent
## 任务一 测试长期记忆

### 1) 记住一个概念（写需要审批）
BODY=$(printf '# 调用链\n路由→Service→仓储（内存假库）。')
   node --import tsx src/cli.ts invoke memory_remember "$(jq -nc --arg b "$BODY" '{id:"concepts/login-flow",type:"Code Concept",title:"登录调用链",description:"从路由到假数据库的登录主链",tags:["auth"],body:$b}')"

node --import tsx src/cli.ts approvals decide <approvalId> --decision approve --actor trainer-1 --roles maintainer

node --import tsx src/cli.ts approvals execute <approvalId>

### 2) 新进程回读（＝新 Run）
node --import tsx src/cli.ts invoke memory_recall '{"query":"登录"}'

### 3) 全量测试
npm test


## 任务二 RAG

### 0) 前置：RAG 先入库（首次或改语料后）
cd ../rag && python ingest.py --overwrite && cd ../project3/v2

### 1) 跑一个业务 Run（Loop 自主决定调 rag_query）
node --import tsx src/cli.ts agent "登录会话有效期是多少？给我带出处的答复" --no-route --decider real

### 2) 观测：看这次 Run 里 rag_query 被调用
node --import tsx src/cli.ts trace <trace_id>
   # 出现三条 tool_name=rag_query：pi.tool_execution_start → tool.completed → pi.tool_execution_end

### 3) 全量测试
npm test
