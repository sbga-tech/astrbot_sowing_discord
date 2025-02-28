# 本地持久化缓存聊天记录id, 缓存后10分钟后查询是否满足条件, 满足条件后转发并删除缓存
from ..config import TEMP_DIR, WAITING_TIME
import os
import json
import time

class LocalCache:
    def __init__(self):
        self.cache_file = os.path.join(TEMP_DIR, "local_cache.json")
        if not os.path.exists(self.cache_file):
            with open(self.cache_file, "w") as f:
                json.dump({}, f)

    async def add_cache(self, message_id: int):
        """添加一条message_id进入缓存, 保存时间"""
        with open(self.cache_file, "r") as f:
            cache = json.load(f)
        cache[message_id] = time.time()
        with open(self.cache_file, "w") as f:
            json.dump(cache, f)
    
    async def get_waiting_messages(self) -> list:
        """获取已经等待WAITING_TIME分钟的消息列表, 并删除缓存"""
        with open(self.cache_file, "r") as f:
            cache = json.load(f)
        
        to_delete = []
        waiting_messages = []
        
        for message_id, timestamp in cache.items():
            if time.time() - timestamp > WAITING_TIME:
                waiting_messages.append(message_id)
                to_delete.append(message_id)
        
        for message_id in to_delete:
            del cache[message_id]
            
        with open(self.cache_file, "w") as f:
            json.dump(cache, f)
            
        return waiting_messages
