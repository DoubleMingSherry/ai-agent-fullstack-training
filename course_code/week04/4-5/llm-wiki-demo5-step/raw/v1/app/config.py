"""项目配置。

注意：这里的 SESSION_TTL_MINUTES 是运维覆盖用的，默认值为空。
真正的默认有效期常量在 app/session.py。
"""

import os

# 允许运维通过环境变量覆盖登录会话有效期（分钟）。
SESSION_TTL_MINUTES = os.environ.get("SESSION_TTL_MINUTES", "")
