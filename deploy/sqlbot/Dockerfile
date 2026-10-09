FROM registry.cn-qingdao.aliyuncs.com/dataease/sqlbot:v1.10.2
COPY patch_runtime.py /tmp/agenthost_patch_runtime.py
RUN python /tmp/agenthost_patch_runtime.py && rm /tmp/agenthost_patch_runtime.py
ENV SQLBOT_DYNAMIC_DB_CONNECT_TIMEOUT=10 SQLBOT_DYNAMIC_DB_READ_TIMEOUT=30
