"""rag 包初始化。

Milvus Lite 内嵌了 gRPC，import 之前先把它的日志级别压下去，
否则标准输出里会混进大量 fork_posix / ev_poll_posix 噪音。
这个设置必须在 import pymilvus 之前生效，所以放在包的 __init__ 里。
"""

import os

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("MILVUS_LOG_LEVEL", "error")
