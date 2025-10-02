# 本地持久化缓存聊天记录id, 缓存后10分钟后查询是否满足条件, 满足条件后转发并删除缓存
from ..config import TEMP_DIR, WAITING_TIME
import os
import json
import time

class LocalCache:
    def __init__(self):
        self.cache_file = os.path.join(TEMP_DIR, "local_cache.json")
        self.WAITING_TIME = WAITING_TIME
        cache_dir = os.path.dirname(self.cache_file)
        os.makedirs(cache_dir, exist_ok=True)
        
        if not os.path.exists(self.cache_file):
            with open(self.cache_file, "w") as f:
                json.dump({}, f)

    async def add_cache(self, message_id: int):
        """添加一条message_id进入缓存, 保存时间"""
        str_message_id = str(message_id)
        with open(self.cache_file, "r") as f:
            cache = json.load(f)
        
        # 键必须是字符串，因为JSON不允许整数作为键
        cache[str_message_id] = time.time()
        
        with open(self.cache_file, "w") as f:
            json.dump(cache, f)
    
    async def get_waiting_messages(self) -> list:
        """获取已经等待WAITING_TIME分钟的消息列表, 并删除缓存"""
        with open(self.cache_file, "r") as f:
            cache = json.load(f)
        
        to_delete = []
        waiting_messages = []
        
        for message_id_str, timestamp in cache.items():
            # 检查消息是否已经等待了足够长的时间
            if time.time() - timestamp > self.WAITING_TIME:
                # 返回时将消息ID从字符串转回整数
                waiting_messages.append(int(message_id_str))
        
        # 【关键修改】移除删除逻辑
        return waiting_messages
        
    async def remove_cache(self, message_id: int):
        """【新增】转发成功或失败后，手动删除指定的 message_id"""
        str_message_id = str(message_id)
        with open(self.cache_file, "r") as f:
            cache = json.load(f)
            
        if str_message_id in cache:
            del cache[str_message_id]
            with open(self.cache_file, "w") as f:
                json.dump(cache, f)
            return True
        return False